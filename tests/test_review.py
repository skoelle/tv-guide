import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from tvguide.db import connect  # noqa: E402
from tvguide.matching import run_match  # noqa: E402
from tvguide.review import apply_override, list_review, review_count, show_key_for, undo_last  # noqa: E402
from tvguide.web.app import create_app  # noqa: E402


def _review_show(cfg, key="sendung 0"):
    con = connect(cfg)
    run_match(con, cfg)
    con.execute("UPDATE show_match SET status='review' WHERE show_id=(SELECT id FROM epg_shows WHERE show_key=?)", (key,))
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
