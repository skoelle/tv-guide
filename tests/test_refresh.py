import os

from tvguide.db import MIGRATIONS, connect
from tvguide.refresh import get_meta, refresh


def test_schema_hat_app_meta(cfg):
    con = connect(cfg)
    assert con.execute("SELECT version FROM schema_version").fetchone()[0] == len(MIGRATIONS)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "app_meta" in tables
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


def test_refresh_erkennt_neue_mtime(cfg):
    con = connect(cfg)
    refresh(con, cfg)
    st = os.stat(cfg.epg_db)
    os.utime(cfg.epg_db, (st.st_atime, st.st_mtime + 10))
    assert refresh(con, cfg)["changed"] is True
    con.close()


def test_refresh_force(cfg):
    con = connect(cfg)
    refresh(con, cfg)
    assert refresh(con, cfg, force=True)["changed"] is True
    con.close()
