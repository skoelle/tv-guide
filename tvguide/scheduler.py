"""Leichter In-App-Scheduler fuer EPG-Import (Intervall) und IMDb-Build (woechentlich).

Laeuft als Daemon-Thread neben dem Webserver, nur wenn `[schedule].enabled`
bzw. `TVGUIDE_SCHEDULE_ENABLED=true` gesetzt ist. Faellige Jobs gehen durch den
gemeinsamen Runner in `tvguide/jobs.py`, damit nie zwei Jobs gleichzeitig laufen.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Config


def parse_hhmm(value: str) -> tuple[int, int]:
    hh, _, mm = value.partition(":")
    return int(hh), int(mm or 0)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def epg_due(last_run: str | None, now: datetime, every_minutes: int) -> bool:
    last = _parse(last_run)
    if last is None:
        return True
    return now - last >= timedelta(minutes=every_minutes)


def imdb_due(last_run: str | None, now: datetime, weekday: int, hhmm: str, tzname: str) -> bool:
    tz = ZoneInfo(tzname)
    local = now.astimezone(tz)
    if local.weekday() != weekday:
        return False
    hh, mm = parse_hhmm(hhmm)
    scheduled = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if local < scheduled:
        return False
    last = _parse(last_run)
    if last is None:
        return True
    return last.astimezone(tz) < scheduled


class Scheduler(threading.Thread):
    def __init__(self, cfg: Config, interval: int = 60, now_fn=None):
        super().__init__(daemon=True, name="tvguide-scheduler")
        self.cfg = cfg
        self.interval = interval
        self._stop = threading.Event()
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # pragma: no cover - thread loop
        while not self._stop.wait(self.interval):
            try:
                self.tick(self._now_fn())
            except Exception:  # noqa: BLE001 - Scheduler darf den Server nicht stoeren
                pass

    def tick(self, now: datetime | None = None) -> None:
        from .db import connect
        from .jobs import running_job, start_job
        from .refresh import get_meta, set_meta

        now = now or self._now_fn()
        con = connect(self.cfg)
        try:
            if running_job(con) is not None:
                return
            meta = get_meta(con)
            if epg_due(meta.get("last_epg_run"), now, self.cfg.schedule_epg_every_minutes):
                if start_job(con, self.cfg, "epg").get("started"):
                    set_meta(con, {"last_epg_run": now.isoformat()})
                return
            if imdb_due(meta.get("last_imdb_run"), now, self.cfg.schedule_imdb_weekday,
                        self.cfg.schedule_imdb_time, self.cfg.timezone):
                if start_job(con, self.cfg, "imdb").get("started"):
                    set_meta(con, {"last_imdb_run": now.isoformat()})
        finally:
            con.close()
