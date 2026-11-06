"""Leichter In-App-Scheduler fuer den EPG-Import und die naechtliche Datenkette.

Laeuft als Daemon-Thread neben dem Webserver, nur wenn `[schedule].enabled`
bzw. `TVGUIDE_SCHEDULE_ENABLED=true` gesetzt ist. Faellige Jobs gehen durch den
gemeinsamen Runner in `tvguide/jobs.py`, damit nie zwei Jobs gleichzeitig laufen.
Der erste EPG-Wechsel eines Tages stoesst zusaetzlich IMDb-Build, Matching und
Posterabruf an.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from .config import Config


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
                if start_job(con, self.cfg, "epg_chain").get("started"):
                    set_meta(con, {"last_epg_run": now.isoformat()})
        finally:
            con.close()
