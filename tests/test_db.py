import sqlite3

import pytest

from tvguide.db import MIGRATIONS, connect


def test_migrations_are_idempotent(cfg):
    con = connect(cfg)
    assert con.execute("SELECT version FROM schema_version").fetchone()[0] == len(MIGRATIONS)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"epg_shows", "show_match", "event_show", "overrides", "saved_searches", "search_hits"} <= tables
    con.close()
    con = connect(cfg)  # zweiter Aufruf aendert nichts
    assert con.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 1
    con.close()


def test_attached_dbs_are_readable(cfg):
    con = connect(cfg)
    assert con.attached == {"epg": "ok", "imdb": "ok"}
    assert con.execute("SELECT count(*) FROM epg.events").fetchone()[0] == 3
    assert con.execute("SELECT primary_title FROM imdb.titles").fetchone()[0] == "The A-Team"
    con.close()


def test_attached_dbs_are_readonly(cfg):
    con = connect(cfg)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO epg.channels VALUES ('x','x',NULL,NULL,'t')")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM imdb.titles")
    con.close()


def test_missing_databases_are_reported(cfg):
    cfg.epg_db.unlink()
    con = connect(cfg)
    assert con.attached["epg"] == "missing"
    assert con.attached["imdb"] == "ok"
    con.close()


def test_epg_replacement_is_picked_up(cfg):
    from conftest import make_epg_db
    con = connect(cfg)
    con.close()
    cfg.epg_db.unlink()
    make_epg_db(cfg.epg_db, events=5)
    con = connect(cfg)
    assert con.execute("SELECT count(*) FROM epg.events").fetchone()[0] == 5
    con.close()
