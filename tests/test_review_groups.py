import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from tvguide.db import connect  # noqa: E402
from tvguide.marks import set_mark  # noqa: E402
from tvguide.matching import run_match  # noqa: E402
from tvguide.review import (apply_overrides, review_count, review_group_count, review_groups,  # noqa: E402
                            undo_last)
from tvguide.web.app import create_app  # noqa: E402


def _three_reviews(cfg):
    imdb = sqlite3.connect(cfg.imdb_db)
    for i in range(3):
        imdb.execute("INSERT INTO title_names VALUES ('tt0086659', ?, ?, 'aka', NULL, NULL)",
                     (f"Sendung {i}", f"sendung {i}"))
    imdb.commit()
    imdb.close()
    con = connect(cfg)
    run_match(con, cfg)
    con.execute("UPDATE show_match SET status='review', tconst='tt0086659'")
    con.commit()
    return con


def test_review_groups_fassen_gleiches_werk_zusammen(cfg):
    con = _three_reviews(cfg)
    assert review_count(con) == 3
    assert review_group_count(con, cfg) == 1
    groups = review_groups(con, cfg)
    assert len(groups) == 1
    group = groups[0]
    assert group["tconst"] == "tt0086659"
    assert group["show_count"] == 3
    assert group["events"] == 3
    assert len(group["show_keys"]) == 3
    assert group["suggestions"]
    con.close()


def test_review_groups_sortieren_alphabetisch(cfg):
    con = _three_reviews(cfg)
    imdb = sqlite3.connect(cfg.imdb_db)
    imdb.execute("INSERT INTO titles VALUES ('tt0000002','tvSeries','Aardvark',NULL,2020,NULL,NULL,NULL,NULL,NULL)")
    imdb.commit()
    imdb.close()
    show_id = con.execute("SELECT id FROM epg_shows WHERE show_key = 'sendung 2'").fetchone()[0]
    con.execute("UPDATE show_match SET tconst = 'tt0000002' WHERE show_id = ?", (show_id,))
    con.commit()
    groups = review_groups(con, cfg)
    assert [g["title"] for g in groups] == ["Aardvark", "The A-Team"]
    con.close()


def test_bulk_override_und_batch_undo(cfg):
    con = _three_reviews(cfg)
    group = review_groups(con, cfg)[0]
    assert apply_overrides(con, group["show_keys"], "tt0086659") == 3
    assert review_group_count(con, cfg) == 0
    rows = con.execute("SELECT batch_id FROM override_log").fetchall()
    assert len(rows) == 3
    assert len({r["batch_id"] for r in rows}) == 1
    assert undo_last(con) == group["show_keys"][-1]
    assert review_group_count(con, cfg) == 1
    assert con.execute("SELECT count(*) FROM override_log").fetchone()[0] == 0
    con.close()


def test_api_group_confirm_und_reject(cfg):
    con = _three_reviews(cfg)
    con.close()
    client = TestClient(create_app(cfg))
    r = client.post("/api/review/confirm",
                    data={"show_keys": "sendung 0,sendung 1", "tconst": "tt0086659"})
    assert r.status_code == 200
    assert r.json()["applied"] == 2
    con = connect(cfg)
    assert review_count(con) == 1
    con.close()
    r = client.post("/api/review/reject", data={"show_keys": "sendung 2"})
    assert r.status_code == 200
    con = connect(cfg)
    assert review_group_count(con, cfg) == 0
    con.close()


def test_group_http_fallback_und_template(cfg):
    _three_reviews(cfg).close()
    con = connect(cfg)
    set_mark(con, "tt9999999", "format")
    con.close()
    client = TestClient(create_app(cfg))
    html = client.get("/review").text
    assert "3 Shows" in html
    assert "Einzelne Shows bearbeiten" in html
    assert 'action="/review/group/confirm"' in html
    assert 'action="/review/group/reject"' in html
    assert "Manuelle Format-Markierungen (1)" in html
    mark_start = html.index('<details class="box"')
    assert " open" not in html[mark_start:html.index("</details>", mark_start)]

    r = client.post("/review/group/confirm",
                    data={"show_keys": "sendung 0,sendung 1,sendung 2", "tconst": "tt0086659"},
                    follow_redirects=False)
    assert r.status_code == 303
    con = connect(cfg)
    assert review_group_count(con, cfg) == 0
    con.close()
