"""TVHeadend-JSON-API: Serien-Timer aus der Werk-Detailansicht anlegen."""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import Config


class TvheadendError(RuntimeError):
    """Fehler bei der Kommunikation mit TVHeadend."""


def derive_api_base(epg_url: str) -> str:
    """Leitet die TVHeadend-Basis-URL aus der XMLTV-URL ab."""
    parts = urllib.parse.urlsplit(epg_url.strip())
    if not parts.scheme or not parts.netloc:
        return ""
    path = parts.path or ""
    marker = path.find("/xmltv")
    if marker >= 0:
        path = path[:marker]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


def api_base(cfg: Config) -> str:
    """Konfigurierte Basis-URL oder Ableitung aus `[epg].url`."""
    explicit = (cfg.tvh_url or "").strip().rstrip("/")
    if explicit.endswith("/api"):
        explicit = explicit[:-4].rstrip("/")
    return explicit or derive_api_base(cfg.epg_url)


def _entries(data: Any) -> list[dict]:
    if isinstance(data, dict):
        rows = data.get("entries")
        return rows if isinstance(rows, list) else []
    return data if isinstance(data, list) else []


def _first_uuid(data: Any) -> str | None:
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return str(data[0]) if data else None
    if isinstance(data, dict):
        for key in ("uuid", "id"):
            value = data.get(key)
            if isinstance(value, list) and value:
                return str(value[0])
            if isinstance(value, str) and value:
                return value
        for key in ("entries", "items"):
            uuid = _first_uuid(data.get(key))
            if uuid:
                return uuid
    return None


def _timestamp(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number // 1000 if number > 10_000_000_000 else number


class TvheadendClient:
    """Kleiner Client fuer die TVHeadend-JSON-API (nur Standardbibliothek)."""

    def __init__(self, cfg: Config, timeout: int = 10):
        self.base = api_base(cfg)
        self.user = (cfg.tvh_user or cfg.epg_user or "").strip()
        self.password = os.environ.get("TVGUIDE_TVH_PASSWORD", "")
        self.dvr_config = (cfg.tvh_dvr_config or "").strip()
        self.timeout = timeout

    def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.base:
            raise TvheadendError("TVHeadend ist nicht konfiguriert (URL in [tvheadend] oder [epg] fehlt).")
        url = f"{self.base}/api/{path.lstrip('/')}"
        data = urllib.parse.urlencode(params or {}).encode() if params is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
        if self.user:
            token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise TvheadendError(
                    "TVHeadend-Anmeldung fehlgeschlagen oder keine DVR-Berechtigung."
                ) from exc
            raise TvheadendError(f"TVHeadend antwortet mit HTTP {exc.code}.") from exc
        except urllib.error.URLError as exc:
            raise TvheadendError(f"TVHeadend nicht erreichbar: {exc.reason}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TvheadendError("Ungueltige Antwort von TVHeadend.") from exc

    def _event_candidates(self, channel_id: str, title: str) -> list[dict]:
        base_params = {"channel": channel_id, "limit": "1000", "sort": "start", "dir": "asc"}
        try:
            entries = _entries(self._request(
                "epg/events/grid", {**base_params, "title": f"^{re.escape(title)}$"}))
        except TvheadendError:
            entries = []
        if entries:
            return entries
        return _entries(self._request("epg/events/grid", base_params))

    def _title_matches(self, events: list[dict], channel_id: str, title: str) -> list[dict]:
        return [
            event for event in events
            if str(event.get("channelUuid") or event.get("channel") or "") == str(channel_id)
            and str(event.get("title") or "").strip() == title.strip()
        ]

    def find_event(self, channel_id: str, title: str, start_ts: int, stop_ts: int) -> dict:
        matches = self._title_matches(self._event_candidates(channel_id, title), channel_id, title)
        if not matches:
            matches = self._title_matches(
                _entries(self._request("epg/events/grid", {"channel": channel_id, "limit": "5000"})),
                channel_id, title,
            )
        exact = [
            event for event in matches
            if abs((_timestamp(event.get("start")) or 0) - int(start_ts)) <= 2
            and (not event.get("stop") or abs((_timestamp(event.get("stop")) or 0) - int(stop_ts)) <= 2)
        ]
        if len(exact) == 1:
            return exact[0]
        near = [
            event for event in matches
            if abs((_timestamp(event.get("start")) or 0) - int(start_ts)) <= 300
            and (not event.get("stop") or abs((_timestamp(event.get("stop")) or 0) - int(stop_ts)) <= 300)
        ]
        if len(exact) > 1:
            raise TvheadendError("Der Termin ist in TVHeadend nicht eindeutig.")
        if len(near) == 1:
            return near[0]
        raise TvheadendError("Der Termin wurde in TVHeadend nicht gefunden. EPG eventuell veraltet.")

    def resolve_dvr_config(self) -> str:
        if self.dvr_config:
            return self.dvr_config
        entries = _entries(self._request("dvr/config/grid", {"limit": "1000"}))
        if not entries:
            raise TvheadendError("Kein DVR-Profil in TVHeadend gefunden.")
        for entry in entries:
            if entry.get("default") in (True, 1, "1"):
                uuid = _first_uuid(entry)
                if uuid:
                    return uuid
        for entry in entries:
            uuid = _first_uuid(entry)
            if uuid:
                return uuid
        raise TvheadendError("Kein DVR-Profil in TVHeadend gefunden.")

    def find_existing_autorec(self, event: dict) -> str | None:
        title = str(event.get("title") or "").strip()
        channel_uuid = str(event.get("channelUuid") or "")
        channel_name = str(event.get("channelName") or "")
        if not title:
            return None
        try:
            entries = _entries(self._request("dvr/autorec/grid", {"limit": "1000"}))
        except TvheadendError:
            return None
        for entry in entries:
            pattern = str(entry.get("title") or "").strip()
            if not pattern:
                continue
            try:
                if not re.fullmatch(pattern, title, re.IGNORECASE):
                    continue
            except re.error:
                if pattern.strip("^$").lower() != title.lower():
                    continue
            channel = str(entry.get("channel") or entry.get("channelName") or "")
            if channel and channel not in (channel_uuid, channel_name):
                continue
            uuid = _first_uuid(entry)
            if uuid:
                return uuid
        return None

    def _autorec_entries(self) -> list[dict]:
        try:
            return _entries(self._request("dvr/autorec/grid", {"limit": "1000"}))
        except TvheadendError:
            return []

    def _title_matches_pattern(self, pattern: str, title: str) -> bool:
        try:
            return bool(re.fullmatch(pattern, title, re.IGNORECASE))
        except re.error:
            return pattern.strip("^$").lower() == title.lower()

    def find_existing_autorec_title(self, title: str, channel_id: str = "") -> str | None:
        title = title.strip()
        if not title:
            return None
        for entry in self._autorec_entries():
            pattern = str(entry.get("title") or "").strip()
            if not pattern or not self._title_matches_pattern(pattern, title):
                continue
            channel = str(entry.get("channel") or "")
            if channel_id:
                if channel != channel_id:
                    continue
            elif channel:
                continue
            uuid = _first_uuid(entry)
            if uuid:
                return uuid
        return None

    def create_series_timer(self, event: dict, config_uuid: str) -> str | None:
        event_id = event.get("eventId")
        if event_id is None:
            raise TvheadendError("TVHeadend hat keine Event-ID geliefert.")
        data = self._request(
            "dvr/autorec/create_by_series",
            {"event_id": str(event_id), "config_uuid": config_uuid},
        )
        if isinstance(data, dict) and data.get("error"):
            raise TvheadendError(str(data["error"]))
        uuid = _first_uuid(data)
        if not uuid:
            raise TvheadendError("TVHeadend hat den Serien-Timer nicht angelegt.")
        return uuid

    def create_series_rule(self, title: str, channel_id: str, config_uuid: str) -> str | None:
        conf = {
            "enabled": 1,
            "name": title,
            "title": f"^{re.escape(title)}$",
            "channel": channel_id,
            "config_name": config_uuid,
        }
        data = self._request(
            "dvr/autorec/create",
            {"conf": json.dumps(conf, ensure_ascii=False)},
        )
        if isinstance(data, dict) and data.get("error"):
            raise TvheadendError(str(data["error"]))
        uuid = _first_uuid(data)
        if not uuid:
            raise TvheadendError("TVHeadend hat den Serien-Timer nicht angelegt.")
        return uuid

    def create_event_timer(self, event: dict, config_uuid: str) -> str | None:
        event_id = event.get("eventId")
        if event_id is None:
            raise TvheadendError("TVHeadend hat keine Event-ID geliefert.")
        data = self._request(
            "dvr/entry/create_by_event",
            {"event_id": str(event_id), "config_uuid": config_uuid},
        )
        if isinstance(data, dict) and data.get("error"):
            raise TvheadendError(str(data["error"]))
        uuid = _first_uuid(data)
        if not uuid:
            raise TvheadendError("TVHeadend hat die Aufnahme nicht angelegt.")
        return uuid

    def _existing_recording(self, event: dict) -> str | None:
        uuid = event.get("dvrUuid")
        if not uuid:
            return None
        state = str(event.get("dvrState") or "").lower()
        if "error" in state or state.startswith("failed"):
            return None
        return str(uuid)

    def record_series(self, channel_id: str, title: str, start_ts: int, stop_ts: int) -> dict:
        event = self.find_event(channel_id, title, start_ts, stop_ts)
        existing = self.find_existing_autorec(event)
        if existing:
            return {"kind": "series", "created": False, "event_id": event.get("eventId"),
                    "autorec_uuid": existing}
        config_uuid = self.resolve_dvr_config()
        uuid = self.create_series_timer(event, config_uuid)
        return {"kind": "series", "created": True, "event_id": event.get("eventId"),
                "autorec_uuid": uuid}

    def record_event(self, channel_id: str, title: str, start_ts: int, stop_ts: int) -> dict:
        event = self.find_event(channel_id, title, start_ts, stop_ts)
        existing = self._existing_recording(event)
        if existing:
            return {"kind": "event", "created": False, "event_id": event.get("eventId"),
                    "dvr_uuid": existing}
        config_uuid = self.resolve_dvr_config()
        uuid = self.create_event_timer(event, config_uuid)
        return {"kind": "event", "created": True, "event_id": event.get("eventId"),
                "dvr_uuid": uuid}

    def record_series_title(self, title: str, channel_id: str = "") -> dict:
        title = title.strip()
        if not title:
            raise TvheadendError("Kein Serientitel vorhanden.")
        existing = self.find_existing_autorec_title(title, channel_id)
        scope = "channel" if channel_id else "all"
        if existing:
            return {"kind": "series", "scope": scope, "created": False, "autorec_uuid": existing}
        config_uuid = self.resolve_dvr_config()
        uuid = self.create_series_rule(title, channel_id, config_uuid)
        return {"kind": "series", "scope": scope, "created": True, "autorec_uuid": uuid}
