import sqlite3
from dataclasses import replace
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from tvguide.db import connect  # noqa: E402
from tvguide.matching import run_match  # noqa: E402
from tvguide.web.app import create_app  # noqa: E402


def _review_cfg(cfg, *, token="secret", sql=False):
    epg = sqlite3.connect(cfg.epg_db)
    epg.execute("UPDATE events SET title='Das A-Team' WHERE rowid = 1")
    epg.commit()
    epg.close()
    con = connect(cfg)
    run_match(con, cfg)
    con.execute(
        "UPDATE show_match SET status='review', tconst='tt0086659' "
        "WHERE show_id=(SELECT id FROM epg_shows WHERE show_key='das a team')")
    con.commit()
    con.close()
    return replace(cfg, support_token=token, support_sql_enabled=sql)


def _headers(token="secret"):
    return {"X-Support-Token": token}


def test_support_disabled_without_token(cfg):
    connect(cfg).close()
    client = TestClient(create_app(cfg))
    assert client.get("/api/support/overview").status_code == 404
    assert client.get("/api/support/review").status_code == 404


def test_support_rejects_wrong_token(cfg):
    cfg = _review_cfg(cfg)
    client = TestClient(create_app(cfg))
    assert client.get("/api/support/overview", headers=_headers("wrong")).status_code == 401
    assert client.get("/api/support/overview").status_code == 401


def test_support_overview_and_review(cfg):
    cfg = _review_cfg(cfg)
    client = TestClient(create_app(cfg))
    overview = client.get("/api/support/overview", headers=_headers()).json()
    assert overview["app"]["shows"] == 3
    assert overview["support"]["sql_enabled"] is False

    data = client.get("/api/support/review", headers=_headers()).json()
    assert data["total"] == 1
    entry = data["entries"][0]
    assert entry["show_key"] == "das a team"
    assert entry["tconst"] == "tt0086659"
    assert entry["raw_titles"]
    assert entry["samples"]
    assert entry["suggestions"]


def test_support_show_and_work(cfg):
    cfg = _review_cfg(cfg)
    client = TestClient(create_app(cfg))
    show = client.get(f"/api/support/shows/{quote('das a team')}", headers=_headers())
    assert show.status_code == 200
    assert show.json()["show_key"] == "das a team"
    assert show.json()["events"] == 1

    work = client.get("/api/support/works/tt0086659", headers=_headers())
    assert work.status_code == 200
    assert work.json()["tconst"] == "tt0086659"
    assert work.json()["shows"][0]["show_key"] == "das a team"
    assert work.json()["airings"]
    assert client.get("/api/support/works/tt9999999", headers=_headers()).status_code == 404


def test_support_sql_is_opt_in_and_read_only(cfg):
    cfg = _review_cfg(cfg, sql=False)
    client = TestClient(create_app(cfg))
    assert client.get("/api/support/sql", params={"sql": "SELECT 1"},
                      headers=_headers()).status_code == 404

    cfg = _review_cfg(cfg, sql=True)
    client = TestClient(create_app(cfg))
    ok = client.get("/api/support/sql", params={"sql": "SELECT count(*) AS n FROM epg_shows"},
                    headers=_headers())
    assert ok.status_code == 200
    assert ok.json()["rows"][0]["n"] == 3

    for sql in ("DELETE FROM show_match", "PRAGMA table_info(epg_shows)",
                "ATTACH DATABASE 'x' AS x", "INSERT INTO app_meta(key,value) VALUES ('x','y')",
                "SELECT 1; SELECT 2"):
        r = client.get("/api/support/sql", params={"sql": sql}, headers=_headers())
        assert r.status_code == 400, sql

    con = connect(cfg)
    assert con.execute("SELECT count(*) FROM app_meta WHERE key='x'").fetchone()[0] == 0
    con.close()


def test_status_page_shows_support_enabled(cfg):
    cfg = _review_cfg(cfg, sql=True)
    client = TestClient(create_app(cfg))
    html = client.get("/status").text
    assert "Support-API" in html
    assert "SQL aktiv" in html
