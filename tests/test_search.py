import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from tvguide.browse import apply_filters, load_catalog  # noqa: E402
from tvguide.db import connect  # noqa: E402
from tvguide.matching import run_match  # noqa: E402
from tvguide.review import apply_override  # noqa: E402
from tvguide.search import (create_search, delete_search, effective_filters,  # noqa: E402
                            get_search, list_searches, matched_works, normalize_filter, run_searches,
                            update_search)
from tvguide.web.app import create_app  # noqa: E402


def _confirmed(cfg):
    con = connect(cfg)
    run_match(con, cfg)
    apply_override(con, "sendung 0", "tt0086659")
    return con


def test_normalize_filter_validiert(cfg):
    assert normalize_filter({"type": "film,serie", "decade": "1980,1990", "retro": "1"}, cfg) == {
        "type": ["film", "serie"], "decade": [1980, 1990], "group": None, "genre": None,
        "retro": True, "q": ""}
    with pytest.raises(ValueError):
        normalize_filter({"unbekannt": 1}, cfg)
    with pytest.raises(ValueError):
        normalize_filter({"type": "buch"}, cfg)
    with pytest.raises(ValueError):
        normalize_filter({"group": "gibts-nicht"}, cfg)


def test_effective_filters_status():
    fj = {"type": None, "decade": None, "group": None, "genre": None, "retro": False, "q": ""}
    assert effective_filters(fj, False)["status"] == {"sicher"}
    assert effective_filters(fj, True)["status"] == {"sicher", "unsicher"}


def test_crud(cfg):
    con = connect(cfg)
    sid = create_search(con, cfg, "Achtziger", {"decade": "1980", "type": "film"}, notify=True)
    s = get_search(con, sid)
    assert s["name"] == "Achtziger" and s["notify"] is True
    assert s["filter"]["decade"] == [1980]
    assert "decade=1980" in s["query"] and "type=film" in s["query"]
    update_search(con, sid, name="Neu", include_review=True, notify=False)
    s = get_search(con, sid)
    assert s["name"] == "Neu" and s["include_review"] is True and s["notify"] is False
    assert [x["id"] for x in list_searches(con)] == [sid]
    delete_search(con, sid)
    assert get_search(con, sid) is None
    con.close()


def test_run_searches_schreibt_treffer(cfg):
    con = _confirmed(cfg)
    sid = create_search(con, cfg, "Alles", {})
    assert run_searches(con, cfg)[0] == {"id": sid, "name": "Alles", "hits": 1, "new": 1}
    assert run_searches(con, cfg)[0]["new"] == 0  # beim zweiten Lauf nichts Neues
    con.close()


def test_treffer_gleich_filteransicht(cfg):
    con = _confirmed(cfg)
    fj = normalize_filter({"genre": "Action"}, cfg)
    works = matched_works(con, cfg, fj, include_review=False)
    direct = apply_filters(load_catalog(con, cfg), effective_filters(fj, False), cfg)
    assert {w["tconst"] for w in works} == {w["tconst"] for w in direct}
    con.close()


def test_http_suchen_anlegen_und_loeschen(cfg):
    _confirmed(cfg).close()
    client = TestClient(create_app(cfg))
    assert client.get("/searches").status_code == 200
    r = client.post("/searches", data={"name": "Serien", "type": "serie", "include_review": "on"},
                    follow_redirects=False)
    assert r.status_code == 303
    con = connect(cfg)
    searches = list_searches(con)
    assert len(searches) == 1 and searches[0]["name"] == "Serien"
    assert searches[0]["filter"]["type"] == ["serie"] and searches[0]["include_review"] is True
    sid = searches[0]["id"]
    con.close()
    assert client.post(f"/searches/{sid}/delete", follow_redirects=False).status_code == 303
    con = connect(cfg)
    assert list_searches(con) == []
    con.close()
