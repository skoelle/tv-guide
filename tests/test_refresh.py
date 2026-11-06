import sqlite3
from datetime import datetime, timezone

import pytest

from tvguide.db import MIGRATIONS, connect
from tvguide.refresh import get_meta, refresh

CHAIN_DAY = datetime(2026, 11, 7, 1, 0, tzinfo=timezone.utc)


def test_schema_hat_app_meta_und_jobs(cfg):
    con = connect(cfg)
    assert con.execute("SELECT version FROM schema_version").fetchone()[0] == len(MIGRATIONS)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"app_meta", "jobs"} <= tables
    con.close()


def test_refresh_laeuft_und_cached(cfg):
    con = connect(cfg)
    first = refresh(con, cfg)
    assert first["changed"] is True
    assert first["shows"] == 3
    assert get_meta(con).get("last_refresh")
    second = refresh(con, cfg)
    assert second["changed"] is False
    assert second["shows"] == 3
    con.close()


def _update(db, sql, params=()):
    con = sqlite3.connect(db)
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def test_refresh_erkennt_neuen_epg_sha1(cfg):
    con = connect(cfg)
    refresh(con, cfg)
    con.close()
    _update(cfg.epg_db, "UPDATE imports SET xml_sha1 = 'abc'")
    con = connect(cfg)
    assert refresh(con, cfg)["changed"] is True
    con.close()


def test_refresh_erkennt_neuen_imdb_stand(cfg):
    con = connect(cfg)
    refresh(con, cfg)
    con.close()
    _update(cfg.imdb_db, "UPDATE meta SET value = '2026-11-06' WHERE key = 'source_date'")
    con = connect(cfg)
    assert refresh(con, cfg)["changed"] is True
    con.close()


def test_refresh_erkennt_neue_ignorierliste(cfg):
    cfg.ignore_channels_file.write_text("Kabel Eins\n", encoding="utf-8")
    con = connect(cfg)
    refresh(con, cfg)
    con.close()
    cfg.ignore_channels_file.write_text("Kabel Eins\nTELE 5\n", encoding="utf-8")
    con = connect(cfg)
    assert refresh(con, cfg)["changed"] is True
    con.close()


def test_epg_unveraendert_refresht_trotzdem(cfg, monkeypatch):
    from tvguide import pipeline

    cfg.ignore_channels_file.write_text("Kabel Eins\n", encoding="utf-8")
    con = connect(cfg)
    refresh(con, cfg)
    con.close()

    cfg.ignore_channels_file.write_text("TELE 5\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "_latest_epg_sha1", lambda path: "same")
    monkeypatch.setattr(pipeline, "_run_stream", lambda cmd, log, display=None: None)
    message = pipeline.run_epg_job(cfg, log=lambda line: None)
    assert message.startswith("EPG unchanged; refresh:")
    assert "none 3" in message


def test_refresh_force(cfg):
    con = connect(cfg)
    refresh(con, cfg)
    assert refresh(con, cfg, force=True)["changed"] is True
    con.close()


def _patch_imdb_job(monkeypatch, poster_job):
    from tvguide import pipeline, posters

    monkeypatch.setattr(pipeline, "_check_disk", lambda path: None)
    monkeypatch.setattr(pipeline, "_run_stream", lambda cmd, log, display=None: None)
    monkeypatch.setattr(pipeline, "_refresh", lambda cfg, log: "refresh: unchanged")
    monkeypatch.setattr(posters, "run_posters_job", poster_job)


def test_imdb_job_fetches_posters(cfg, monkeypatch):
    from tvguide import pipeline

    calls = []

    def fake_posters(cfg, log=print, *, prune=False):
        calls.append(prune)
        return "posters: 1 geladen"

    _patch_imdb_job(monkeypatch, fake_posters)
    message = pipeline.run_imdb_job(cfg, log=lambda line: None)

    assert calls == [True]
    assert message == "IMDb build done; refresh: unchanged; posters: 1 geladen"


def test_imdb_job_skips_posters_when_disabled(cfg, monkeypatch):
    from tvguide import pipeline

    cfg.poster_enabled = False

    def fake_posters(cfg, log=print, *, prune=False):
        raise AssertionError("poster job must not run")

    _patch_imdb_job(monkeypatch, fake_posters)
    message = pipeline.run_imdb_job(cfg, log=lambda line: None)

    assert message == "IMDb build done; refresh: unchanged"


def test_imdb_job_survives_poster_error(cfg, monkeypatch):
    from tvguide import pipeline

    def fake_posters(cfg, log=print, *, prune=False):
        raise RuntimeError("tmdb down")

    _patch_imdb_job(monkeypatch, fake_posters)
    message = pipeline.run_imdb_job(cfg, log=lambda line: None)

    assert message == "IMDb build done; refresh: unchanged; posters: failed (RuntimeError: tmdb down)"


def _patch_epg_chain(monkeypatch, shas):
    from tvguide import pipeline

    values = iter(shas)
    monkeypatch.setattr(pipeline, "_latest_epg_sha1", lambda path: next(values))
    monkeypatch.setattr(pipeline, "_run_stream", lambda cmd, log, display=None: None)
    monkeypatch.setattr(pipeline, "_refresh", lambda cfg, log: "refresh: changed")
    return pipeline


def test_epg_chain_first_change_starts_imdb(cfg, monkeypatch):
    pipeline = _patch_epg_chain(monkeypatch, ["old", "new"])
    calls = []
    monkeypatch.setattr(
        pipeline, "run_imdb_job",
        lambda cfg, log=print: (calls.append(True) or "imdb: done"))

    message = pipeline.run_epg_chain_job(cfg, log=lambda line: None, now=CHAIN_DAY)

    assert calls == [True]
    assert message == "EPG imported; refresh: changed; imdb: done"
    con = connect(cfg)
    assert get_meta(con).get("last_epg_chain_date") == "2026-11-07"
    con.close()


def test_epg_chain_only_once_per_day(cfg, monkeypatch):
    pipeline = _patch_epg_chain(monkeypatch, ["old", "new", "old", "new"])
    calls = []
    monkeypatch.setattr(
        pipeline, "run_imdb_job",
        lambda cfg, log=print: (calls.append(True) or "imdb: done"))

    first = pipeline.run_epg_chain_job(cfg, log=lambda line: None, now=CHAIN_DAY)
    second = pipeline.run_epg_chain_job(cfg, log=lambda line: None, now=CHAIN_DAY)

    assert calls == [True]
    assert first.endswith("imdb: done")
    assert second == "EPG imported; refresh: changed"


def test_epg_chain_skips_imdb_without_change(cfg, monkeypatch):
    pipeline = _patch_epg_chain(monkeypatch, ["same", "same"])
    monkeypatch.setattr(
        pipeline, "run_imdb_job",
        lambda cfg, log=print: (_ for _ in ()).throw(AssertionError("must not run")))

    message = pipeline.run_epg_chain_job(cfg, log=lambda line: None, now=CHAIN_DAY)

    assert message == "EPG unchanged; refresh: changed"
    con = connect(cfg)
    assert "last_epg_chain_date" not in get_meta(con)
    con.close()


def test_epg_chain_runs_again_next_day(cfg, monkeypatch):
    pipeline = _patch_epg_chain(monkeypatch, ["old", "new", "old", "new"])
    calls = []
    monkeypatch.setattr(
        pipeline, "run_imdb_job",
        lambda cfg, log=print: (calls.append(True) or "imdb: done"))

    pipeline.run_epg_chain_job(cfg, log=lambda line: None, now=CHAIN_DAY)
    pipeline.run_epg_chain_job(
        cfg, log=lambda line: None, now=datetime(2026, 11, 8, 1, 0, tzinfo=timezone.utc))

    assert calls == [True, True]


def test_epg_chain_failure_allows_retry(cfg, monkeypatch):
    pipeline = _patch_epg_chain(monkeypatch, ["old", "new"])

    def fail(cfg, log=print):
        raise RuntimeError("imdb down")

    monkeypatch.setattr(pipeline, "run_imdb_job", fail)

    with pytest.raises(RuntimeError, match="imdb down"):
        pipeline.run_epg_chain_job(cfg, log=lambda line: None, now=CHAIN_DAY)

    con = connect(cfg)
    assert get_meta(con).get("last_epg_chain_date") == ""
    con.close()
