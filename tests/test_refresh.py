import sqlite3

from tvguide.db import MIGRATIONS, connect
from tvguide.refresh import get_meta, refresh


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


def test_refresh_force(cfg):
    con = connect(cfg)
    refresh(con, cfg)
    assert refresh(con, cfg, force=True)["changed"] is True
    con.close()
