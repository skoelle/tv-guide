from datetime import datetime, timezone

from tvguide.scheduler import Scheduler, epg_due


def test_epg_due():
    now = datetime(2026, 11, 2, 11, 0, tzinfo=timezone.utc)
    assert epg_due(None, now, 60) is True
    assert epg_due("2026-11-02T11:30:00+01:00", now, 60) is False
    assert epg_due("2026-11-02T10:00:00+01:00", now, 60) is True
    assert epg_due("kaputt", now, 60) is True


def test_tick_starts_epg_chain(cfg, monkeypatch):
    from tvguide import jobs

    calls = []

    def fake_start(con, cfg, kind):
        calls.append(kind)
        return {"started": True}

    monkeypatch.setattr(jobs, "start_job", fake_start)
    scheduler = Scheduler(cfg)
    scheduler.tick(datetime(2026, 11, 7, 1, 0, tzinfo=timezone.utc))

    assert calls == ["epg_chain"]
