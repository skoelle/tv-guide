import time

from tvguide import jobs
from tvguide.db import connect


def _wait(con, job_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        row = jobs.get_job(con, job_id)
        if row is not None and row["status"] != "running":
            return row
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")


def test_start_and_finish(cfg, monkeypatch):
    def fake_run(cfg, kind, log):
        log("hello from job")
        return "done"

    monkeypatch.setattr(jobs, "run_kind", fake_run)
    con = connect(cfg)
    res = jobs.start_job(con, cfg, "epg")
    assert res["started"] is True
    row = _wait(con, res["id"])
    con.close()
    assert row["status"] == "ok"
    assert row["message"] == "done"
    assert "hello from job" in open(row["log_path"], encoding="utf-8").read()


def test_only_one_job_at_a_time(cfg, monkeypatch):
    def slow_run(cfg, kind, log):
        time.sleep(0.2)
        return "ok"

    monkeypatch.setattr(jobs, "run_kind", slow_run)
    con = connect(cfg)
    first = jobs.start_job(con, cfg, "epg")
    second = jobs.start_job(con, cfg, "imdb")
    assert first["started"] is True
    assert second == {"started": False, "error": "busy"}
    _wait(con, first["id"])
    con.close()


def test_error_is_recorded(cfg, monkeypatch):
    def boom(cfg, kind, log):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(jobs, "run_kind", boom)
    con = connect(cfg)
    res = jobs.start_job(con, cfg, "imdb")
    row = _wait(con, res["id"])
    con.close()
    assert row["status"] == "error"
    assert "kaputt" in row["message"]


def test_unknown_kind(cfg):
    con = connect(cfg)
    assert jobs.start_job(con, cfg, "nope") == {"started": False, "error": "unknown kind"}
    con.close()


def test_recover_stale(cfg):
    con = connect(cfg)
    con.execute("INSERT INTO jobs (kind, status, started_at) VALUES ('epg','running','x')")
    con.commit()
    assert jobs.recover_stale(con) == 1
    row = con.execute("SELECT status FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "error"
    con.close()


def test_recent_jobs_limit_and_detail(cfg, tmp_path):
    log = tmp_path / "job.log"
    log.write_text("zeile 1\nzeile 2\n", encoding="utf-8")
    con = connect(cfg)
    for i in range(30):
        con.execute("INSERT INTO jobs (kind, status, started_at, finished_at, message, log_path)"
                    " VALUES ('epg','ok','x','y','done',?)", (str(log) if i == 29 else None,))
    con.commit()

    recent = jobs.recent_jobs(con)
    assert len(recent) == jobs.JOBS_LIMIT == 24
    assert "log_tail" not in recent[0]

    newest = recent[0]["id"]
    detail = jobs.job_detail(con, newest)
    assert detail is not None
    assert "zeile 2" in detail["log_tail"]
    con.close()


def test_match_kind_forces_refresh(cfg, monkeypatch):
    from tvguide import pipeline

    calls = []

    def fake(cfg, log=print, *, force=False):
        calls.append(force)
        return "ok"

    monkeypatch.setattr(pipeline, "run_refresh_job", fake)
    assert jobs.run_kind(cfg, "match", print) == "ok"
    assert calls == [True]
