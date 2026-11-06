import re
import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from tvguide.db import connect  # noqa: E402
from tvguide.marks import set_mark, undo_last as undo_last_mark  # noqa: E402
from tvguide.matching import run_match  # noqa: E402
from tvguide.review import (apply_override, list_review, mark_as_format, reopen_work, review_count,  # noqa: E402
                            review_entries_for_tconst, show_key_for, undo_last)
from tvguide.web.app import create_app  # noqa: E402

_URL_RE = re.compile(r'\b(?:href|src|action|formaction|hx-(?:get|post|put|delete|patch))="([^"]*)"')


def _relative_urls(html: str) -> list[str]:
    return [u for u in _URL_RE.findall(html)
            if u and not u.startswith(("/", "http://", "https://", "#", "?"))]


def _review_show(cfg, key="sendung 0", tconst=None):
    con = connect(cfg)
    run_match(con, cfg)
    if tconst is None:
        con.execute("UPDATE show_match SET status='review' "
                    "WHERE show_id=(SELECT id FROM epg_shows WHERE show_key=?)", (key,))
    else:
        con.execute("UPDATE show_match SET status='review', tconst=? "
                    "WHERE show_id=(SELECT id FROM epg_shows WHERE show_key=?)", (tconst, key))
    con.commit()
    return con


def test_review_count_liste_und_suche(cfg):
    con = _review_show(cfg)
    assert review_count(con) == 1
    shows = list_review(con, cfg)
    con.close()
    assert len(shows) == 1
    assert shows[0]["show_key"] == "sendung 0"
    assert shows[0]["show_name"] == "Sendung 0"
    assert shows[0]["events"] == 1
    assert shows[0]["samples"][0]["channel"] == "Kabel Eins"


def test_review_entries_for_tconst(cfg):
    con = _review_show(cfg, tconst="tt0086659")
    entries = review_entries_for_tconst(con, cfg, "tt0086659")
    assert review_entries_for_tconst(con, cfg, "tt0000000") == []
    con.close()
    assert len(entries) == 1
    assert entries[0]["show_key"] == "sendung 0"
    assert isinstance(entries[0]["suggestions"], list)


def test_work_page_zeigt_review_box(cfg):
    _review_show(cfg, tconst="tt0086659").close()
    client = TestClient(create_app(cfg))
    html = client.get("/work/tt0086659").text
    assert "Zuordnung prüfen" in html
    assert 'hx-post="/api/review/confirm"' in html
    assert 'hx-post="/api/review/reject"' in html
    assert 'action="/review/sendung 0/confirm"' in html
    assert 'formaction="/review/sendung 0/reject"' in html
    assert 'hx-get="/review/search?scope=work"' in html
    assert 'href="/browse"' in html
    assert _relative_urls(html) == []
    assert 'href="https://www.imdb.com/title/tt0086659/"' in html
    assert 'target="_blank"' in html
    partial = client.get("/review/search", params={"q": "the", "show_key": "sendung 0",
                                                   "scope": "work"}).text
    assert "location.reload()" in partial
    assert 'hx-post="/api/review/confirm"' in partial
    assert 'action="/review/sendung 0/confirm"' in partial
    assert _relative_urls(partial) == []
    assert 'href="https://www.imdb.com/title/tt0086659/"' in partial
    assert 'target="_blank"' in partial


def test_work_page_kategorie_buttons(cfg):
    _review_show(cfg, tconst="tt0086659").close()
    client = TestClient(create_app(cfg))
    html = client.get("/work/tt0086659").text
    assert "Als Format markieren" in html
    assert "Als Serie behalten" not in html

    con = connect(cfg)
    set_mark(con, "tt0086659", "format")
    con.close()

    html = client.get("/work/tt0086659").text
    assert "Als Format markieren" not in html
    assert "Als Serie behalten" in html
    assert "Markierung entfernen" in html


def test_show_key_for(cfg):
    con = _review_show(cfg)
    key = show_key_for(con, "sendung 0")
    show_id = con.execute("SELECT id FROM epg_shows WHERE show_key='sendung 0'").fetchone()[0]
    assert key == "sendung 0"
    assert show_key_for(con, str(show_id)) == "sendung 0"
    assert show_key_for(con, "gibt-es-nicht") is None
    con.close()


def test_confirm_und_reject(cfg):
    con = _review_show(cfg)
    apply_override(con, "sendung 0", "tt0086659")
    row = con.execute("""SELECT sm.status, sm.tconst, sm.method, o.tconst AS ov
                         FROM show_match sm JOIN overrides o ON o.show_key='sendung 0'
                         WHERE sm.show_id=(SELECT id FROM epg_shows WHERE show_key='sendung 0')""").fetchone()
    assert tuple(row) == ("confirmed", "tt0086659", "override", "tt0086659")
    apply_override(con, "sendung 0", None)
    row = con.execute("""SELECT sm.status, sm.tconst FROM show_match sm
                         WHERE sm.show_id=(SELECT id FROM epg_shows WHERE show_key='sendung 0')""").fetchone()
    assert tuple(row) == ("rejected", None)
    con.close()


def test_undo_stellt_vorherigen_zustand_wieder_her(cfg):
    con = _review_show(cfg)
    apply_override(con, "sendung 0", "tt0086659")
    apply_override(con, "sendung 0", "tt0088559")   # zweite Entscheidung
    assert undo_last(con) == "sendung 0"
    row = con.execute("""SELECT sm.status, sm.tconst, o.tconst AS ov
                         FROM show_match sm JOIN overrides o ON o.show_key='sendung 0'
                         WHERE sm.show_id=(SELECT id FROM epg_shows WHERE show_key='sendung 0')""").fetchone()
    assert tuple(row) == ("confirmed", "tt0086659", "tt0086659")  # erste Entscheidung wieder aktiv
    undo_last(con)
    assert con.execute("SELECT count(*) FROM overrides WHERE show_key='sendung 0'").fetchone()[0] == 0
    status = con.execute("""SELECT status FROM show_match WHERE show_id=(SELECT id FROM epg_shows WHERE show_key='sendung 0')""").fetchone()[0]
    assert status == "review"  # Ausgangszustand
    con.close()


def test_undo_ohne_entscheidung(cfg):
    con = connect(cfg)
    assert undo_last(con) is None
    con.close()


def test_http_review_flow(cfg):
    _review_show(cfg).close()
    client = TestClient(create_app(cfg))
    assert client.get("/review").status_code == 200
    r = client.post("/review/sendung 0/confirm", data={"tconst": "tt0086659"}, follow_redirects=False)
    assert r.status_code == 303
    con = connect(cfg)
    assert review_count(con) == 0
    assert con.execute("SELECT tconst FROM overrides WHERE show_key='sendung 0'").fetchone()[0] == "tt0086659"
    con.close()
    r = client.post("/review/undo", follow_redirects=False)
    assert r.status_code == 303
    con = connect(cfg)
    assert review_count(con) == 1
    con.close()


def test_api_review_confirm(cfg):
    _review_show(cfg).close()
    client = TestClient(create_app(cfg))
    r = client.post("/api/review/confirm",
                    data={"show_key": "sendung 0", "tconst": "tt0086659"},
                    follow_redirects=False)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "location" not in r.headers
    con = connect(cfg)
    assert review_count(con) == 0
    assert con.execute("SELECT tconst FROM overrides WHERE show_key='sendung 0'").fetchone()[0] == "tt0086659"
    con.close()


def test_api_review_confirm_validiert_eingaben(cfg):
    _review_show(cfg).close()
    client = TestClient(create_app(cfg))
    r = client.post("/api/review/confirm", data={"show_key": "sendung 0"})
    assert r.status_code == 400
    r = client.post("/api/review/confirm",
                    data={"show_key": "gibt-es-nicht", "tconst": "tt0086659"})
    assert r.status_code == 404
    con = connect(cfg)
    assert review_count(con) == 1
    con.close()


def test_api_review_reject(cfg):
    _review_show(cfg).close()
    client = TestClient(create_app(cfg))
    r = client.post("/api/review/reject", data={"show_key": "sendung 0"},
                    follow_redirects=False)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "location" not in r.headers
    con = connect(cfg)
    assert review_count(con) == 0
    row = con.execute("""SELECT sm.status, sm.tconst FROM show_match sm
                         WHERE sm.show_id=(SELECT id FROM epg_shows WHERE show_key='sendung 0')""").fetchone()
    assert tuple(row) == ("rejected", None)
    con.close()
    r = client.post("/api/review/reject", data={"show_key": "gibt-es-nicht"})
    assert r.status_code == 404


def test_api_review_undo(cfg):
    _review_show(cfg).close()
    client = TestClient(create_app(cfg))
    client.post("/api/review/confirm",
                data={"show_key": "sendung 0", "tconst": "tt0086659"})
    r = client.post("/api/review/undo", follow_redirects=False)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["show_key"] == "sendung 0"
    assert "location" not in r.headers
    con = connect(cfg)
    assert review_count(con) == 1
    con.close()


def test_review_html_nutzt_htmx(cfg):
    _review_show(cfg).close()
    html = TestClient(create_app(cfg)).get("/review").text
    assert 'hx-post="/api/review/confirm"' in html
    assert 'hx-post="/api/review/reject"' in html
    assert 'hx-post="/api/review/undo"' in html
    assert 'name="show_key" value="sendung 0"' in html
    assert 'id="review-list"' in html
    assert _relative_urls(html) == []


def _add_imdb_title(cfg, tconst, title_type, title, genres):
    con = sqlite3.connect(cfg.imdb_db)
    try:
        con.execute("INSERT INTO titles VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (tconst, title_type, title, title, 2020, None, 30, genres, 5.0, 10))
        con.execute("INSERT INTO title_names VALUES (?,?,?,?,?,?)",
                    (tconst, title, title.lower(), "primary", None, None))
        con.commit()
    finally:
        con.close()


def test_format_review_wird_ausgeblendet(cfg):
    _add_imdb_title(cfg, "ttdoc", "tvSeries", "Brisant", "Documentary")
    con = _review_show(cfg)
    con.execute("UPDATE show_match SET tconst='ttdoc' "
                "WHERE show_id=(SELECT id FROM epg_shows WHERE show_key='sendung 0')")
    con.commit()
    assert review_count(con) == 0
    assert list_review(con, cfg) == []
    con.close()


def test_mark_as_format_und_undo(cfg):
    con = _review_show(cfg)
    con.execute("UPDATE show_match SET tconst='tt0086659' "
                "WHERE show_id=(SELECT id FROM epg_shows WHERE show_key='sendung 0')")
    con.commit()
    assert review_count(con) == 1
    assert mark_as_format(con, "sendung 0") == "tt0086659"
    assert review_count(con) == 0
    assert undo_last_mark(con) == "tt0086659"
    assert review_count(con) == 1
    con.close()


def test_work_page_zeigt_imdb_titel_und_tmdb_link(cfg):
    con = _review_show(cfg, tconst="tt0086659")
    con.execute(
        "INSERT INTO posters (tconst, tmdb_id, media_type, poster_path, image_file, status, fetched_at) "
        "VALUES ('tt0086659', 821, 'tv', '/x.jpg', NULL, 'ok', '2026-11-06T00:00:00Z')")
    con.commit()
    con.close()
    html = TestClient(create_app(cfg)).get("/work/tt0086659").text
    assert "IMDb-Titel" in html
    assert "The A-Team" in html
    assert 'href="https://www.themoviedb.org/tv/821"' in html
    assert "TMDB-Seite" in html


def test_work_page_zuordnung_entfernen_button(cfg):
    con = connect(cfg)
    run_match(con, cfg)
    con.execute("UPDATE show_match SET status='confirmed', tconst='tt0086659' "
                "WHERE show_id=(SELECT id FROM epg_shows WHERE show_key='sendung 0')")
    con.commit()
    con.close()
    html = TestClient(create_app(cfg)).get("/work/tt0086659").text
    assert "Zuordnung entfernen" in html
    assert 'hx-post="/api/works/tt0086659/reopen"' in html
    assert "Zuordnung prüfen" not in html
    assert _relative_urls(html) == []


def test_reopen_work(cfg):
    con = _review_show(cfg, tconst="tt0086659")
    apply_override(con, "sendung 0", "tt0086659")
    assert review_count(con) == 0
    assert reopen_work(con, "tt0086659") == 1
    row = con.execute(
        """SELECT sm.status, sm.tconst, sm.method FROM show_match sm
           WHERE sm.show_id=(SELECT id FROM epg_shows WHERE show_key='sendung 0')""").fetchone()
    assert tuple(row) == ("review", None, "reopen")
    assert con.execute("SELECT count(*) FROM overrides").fetchone()[0] == 0
    assert review_count(con) == 1
    assert reopen_work(con, "tt0000000") == 0
    con.close()


def test_api_work_reopen(cfg):
    _review_show(cfg, tconst="tt0086659").close()
    client = TestClient(create_app(cfg))
    r = client.post("/api/works/tt0086659/reopen", follow_redirects=False)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["shows"] == 1
    assert r.json()["review"] == 1
    assert client.get("/work/tt0086659").status_code == 404
    assert client.post("/api/works/tt0000000/reopen").status_code == 404
