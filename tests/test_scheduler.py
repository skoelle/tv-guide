from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tvguide.scheduler import epg_due, imdb_due, parse_hhmm

TZ = "Europe/Berlin"


def _utc(local_str):
    return datetime.fromisoformat(local_str).replace(tzinfo=ZoneInfo(TZ)).astimezone(timezone.utc)


def test_parse_hhmm():
    assert parse_hhmm("04:30") == (4, 30)
    assert parse_hhmm("7") == (7, 0)


def test_epg_due():
    now = _utc("2026-11-02T12:00:00")
    assert epg_due(None, now, 60) is True
    assert epg_due("2026-11-02T11:30:00+01:00", now, 60) is False
    assert epg_due("2026-11-02T10:00:00+01:00", now, 60) is True
    assert epg_due("kaputt", now, 60) is True


def test_imdb_due_monday_0430():
    monday_after = _utc("2026-11-02T04:30:00")
    assert imdb_due(None, monday_after, 0, "04:30", TZ) is True
    # bereits gelaufen
    assert imdb_due(monday_after.isoformat(), monday_after, 0, "04:30", TZ) is False
    # zu frueh am Montag
    assert imdb_due(None, _utc("2026-11-02T04:00:00"), 0, "04:30", TZ) is False
    # falscher Wochentag
    assert imdb_due(None, _utc("2026-11-03T05:00:00"), 0, "04:30", TZ) is False
    # letzte Woche gelaufen -> wieder faellig
    assert imdb_due("2026-10-26T04:31:00+01:00", monday_after, 0, "04:30", TZ) is True
