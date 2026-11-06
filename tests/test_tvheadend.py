import json
from dataclasses import replace

import pytest

from tvguide.tvheadend import TvheadendClient, TvheadendError, api_base, derive_api_base


class FakeClient(TvheadendClient):
    def __init__(self, cfg, responses):
        super().__init__(cfg)
        self.responses = responses
        self.calls = []

    def _request(self, path, params=None):
        self.calls.append((path, params or {}))
        response = self.responses[path]
        return response(params or {}) if callable(response) else response


def _event(**overrides):
    event = {
        "eventId": 7,
        "channelUuid": "c1",
        "channelName": "Kabel Eins",
        "title": "Das A-Team",
        "start": 1793538000,
        "stop": 1793541600,
    }
    event.update(overrides)
    return event


def test_derive_api_base():
    assert derive_api_base("http://tvh:9981/xmltv/channels") == "http://tvh:9981"
    assert derive_api_base("http://tvh/tvheadend/xmltv/channels") == "http://tvh/tvheadend"
    assert derive_api_base("http://tvh:9981") == "http://tvh:9981"
    assert derive_api_base("") == ""


def test_api_base_explicit_and_from_epg(cfg):
    cfg = replace(cfg, epg_url="http://tvh:9981/xmltv/channels", tvh_url="")
    assert api_base(cfg) == "http://tvh:9981"
    cfg = replace(cfg, tvh_url="http://other:9981/api/")
    assert api_base(cfg) == "http://other:9981"


def test_find_event_exact(cfg):
    client = FakeClient(cfg, {"epg/events/grid": {"entries": [
        _event(eventId=8, start=1793541600, stop=1793545200),
        _event(),
    ]}})
    event = client.find_event("c1", "Das A-Team", 1793538000, 1793541600)
    assert event["eventId"] == 7
    assert client.calls[0][1]["title"] == "^Das\\ A\\-Team$"


def test_find_event_fallback_to_channel_query(cfg):
    def grid(params):
        return {"entries": [] if "title" in params else [_event()]}

    client = FakeClient(cfg, {"epg/events/grid": grid})
    assert client.find_event("c1", "Das A-Team", 1793538000, 1793541600)["eventId"] == 7
    assert len(client.calls) == 2


def test_find_event_nicht_gefunden(cfg):
    client = FakeClient(cfg, {"epg/events/grid": {"entries": []}})
    with pytest.raises(TvheadendError, match="nicht gefunden"):
        client.find_event("c1", "Das A-Team", 1793538000, 1793541600)


def test_record_series_legt_timer_an(cfg):
    client = FakeClient(cfg, {
        "epg/events/grid": {"entries": [_event()]},
        "dvr/autorec/grid": {"entries": []},
        "dvr/config/grid": {"entries": [{"uuid": "cfg1", "name": "Default"}]},
        "dvr/autorec/create_by_series": {"uuid": ["auto1"]},
    })
    result = client.record_series("c1", "Das A-Team", 1793538000, 1793541600)
    assert result == {"kind": "series", "created": True, "event_id": 7, "autorec_uuid": "auto1"}
    create = [params for path, params in client.calls if path == "dvr/autorec/create_by_series"]
    assert create == [{"event_id": "7", "config_uuid": "cfg1"}]


def test_record_series_nutzt_vorhandenen_timer(cfg):
    client = FakeClient(cfg, {
        "epg/events/grid": {"entries": [_event()]},
        "dvr/autorec/grid": {"entries": [
            {"uuid": "auto0", "title": "Das A-Team", "channel": "c1"},
            {"uuid": "auto2", "title": "Andere Serie", "channel": "c1"},
        ]},
    })
    result = client.record_series("c1", "Das A-Team", 1793538000, 1793541600)
    assert result == {"kind": "series", "created": False, "event_id": 7, "autorec_uuid": "auto0"}
    assert all(path != "dvr/autorec/create_by_series" for path, _ in client.calls)


def test_record_series_meldet_fehlschlag_ohne_uuid(cfg):
    client = FakeClient(cfg, {
        "epg/events/grid": {"entries": [_event()]},
        "dvr/autorec/grid": {"entries": []},
        "dvr/config/grid": {"entries": [{"uuid": "cfg1"}]},
        "dvr/autorec/create_by_series": {},
    })
    with pytest.raises(TvheadendError, match="nicht angelegt"):
        client.record_series("c1", "Das A-Team", 1793538000, 1793541600)


def test_record_event_legt_einzelaufnahme_an(cfg):
    client = FakeClient(cfg, {
        "epg/events/grid": {"entries": [_event()]},
        "dvr/config/grid": {"entries": [{"uuid": "cfg1"}]},
        "dvr/entry/create_by_event": {"uuid": "dvr1"},
    })
    result = client.record_event("c1", "Das A-Team", 1793538000, 1793541600)
    assert result == {"kind": "event", "created": True, "event_id": 7, "dvr_uuid": "dvr1"}
    create = [params for path, params in client.calls if path == "dvr/entry/create_by_event"]
    assert create == [{"event_id": "7", "config_uuid": "cfg1"}]


def test_record_event_nutzt_vorhandene_aufnahme(cfg):
    client = FakeClient(cfg, {
        "epg/events/grid": {"entries": [_event(dvrUuid="dvr0", dvrState="scheduled")]},
    })
    result = client.record_event("c1", "Das A-Team", 1793538000, 1793541600)
    assert result == {"kind": "event", "created": False, "event_id": 7, "dvr_uuid": "dvr0"}
    assert all(path != "dvr/entry/create_by_event" for path, _ in client.calls)


def test_record_event_meldet_fehlschlag_ohne_uuid(cfg):
    client = FakeClient(cfg, {
        "epg/events/grid": {"entries": [_event()]},
        "dvr/config/grid": {"entries": [{"uuid": "cfg1"}]},
        "dvr/entry/create_by_event": {},
    })
    with pytest.raises(TvheadendError, match="nicht angelegt"):
        client.record_event("c1", "Das A-Team", 1793538000, 1793541600)


def test_record_series_title_alle_sender(cfg):
    client = FakeClient(cfg, {
        "dvr/autorec/grid": {"entries": []},
        "dvr/config/grid": {"entries": [{"uuid": "cfg1"}]},
        "dvr/autorec/create": {"uuid": ["auto1"]},
    })
    result = client.record_series_title("Das A-Team")
    assert result == {"kind": "series", "scope": "all", "created": True, "autorec_uuid": "auto1"}
    call = next(params for path, params in client.calls if path == "dvr/autorec/create")
    conf = json.loads(call["conf"])
    assert conf["title"] == "^Das\\ A\\-Team$"
    assert conf["channel"] == ""
    assert conf["config_name"] == "cfg1"


def test_record_series_title_sender(cfg):
    client = FakeClient(cfg, {
        "dvr/autorec/grid": {"entries": []},
        "dvr/config/grid": {"entries": [{"uuid": "cfg1"}]},
        "dvr/autorec/create": {"uuid": ["auto1"]},
    })
    result = client.record_series_title("Das A-Team", "c1")
    assert result == {"kind": "series", "scope": "channel", "created": True, "autorec_uuid": "auto1"}
    call = next(params for path, params in client.calls if path == "dvr/autorec/create")
    assert json.loads(call["conf"])["channel"] == "c1"


def test_record_series_title_nutzt_vorhandene_regel(cfg):
    client = FakeClient(cfg, {
        "dvr/autorec/grid": {"entries": [
            {"uuid": "auto0", "title": "Das A-Team", "channel": ""},
            {"uuid": "auto2", "title": "Das A-Team", "channel": "c1"},
        ]},
    })
    result = client.record_series_title("Das A-Team")
    assert result == {"kind": "series", "scope": "all", "created": False, "autorec_uuid": "auto0"}
    assert all(path != "dvr/autorec/create" for path, _ in client.calls)


def test_record_series_title_sender_ist_nicht_alle_sender(cfg):
    client = FakeClient(cfg, {
        "dvr/autorec/grid": {"entries": [
            {"uuid": "auto2", "title": "Das A-Team", "channel": "c1"},
        ]},
        "dvr/config/grid": {"entries": [{"uuid": "cfg1"}]},
        "dvr/autorec/create": {"uuid": ["auto1"]},
    })
    result = client.record_series_title("Das A-Team")
    assert result["created"] is True


def test_record_series_nutzt_konfiguriertes_dvr_profil(cfg):
    cfg = replace(cfg, tvh_dvr_config="Mein Profil")
    client = FakeClient(cfg, {
        "epg/events/grid": {"entries": [_event()]},
        "dvr/autorec/grid": {"entries": []},
        "dvr/autorec/create_by_series": {"uuid": ["auto1"]},
    })
    client.record_series("c1", "Das A-Team", 1793538000, 1793541600)
    create = [params for path, params in client.calls if path == "dvr/autorec/create_by_series"]
    assert create == [{"event_id": "7", "config_uuid": "Mein Profil"}]
