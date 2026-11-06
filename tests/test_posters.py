import sqlite3

import httpx
import pytest

from tvguide.db import connect
from tvguide.posters import PosterError, TmdbClient, _auth, fetch_posters


def _show(con, key: str, tconst: str) -> None:
    cur = con.execute("INSERT INTO epg_shows (show_key, show_name, first_seen) VALUES (?,?,?)",
                      (key, key, "2026-11-01T00:00:00Z"))
    con.execute("INSERT INTO show_match (show_id, tconst, status, updated_at) VALUES (?,?,?,?)",
                (cur.lastrowid, tconst, "auto", "2026-11-01T00:00:00Z"))


def _add_movie(cfg, tconst: str, title: str) -> None:
    db = sqlite3.connect(cfg.imdb_db)
    db.execute("INSERT INTO titles VALUES (?,?,?,?,?,?,?,?,?,?)",
               (tconst, "movie", title, title, 1994, None, 142, "Drama", 9.3, 2800000))
    db.commit()
    db.close()


def _handler(calls: list[str], *, empty: str | None = None, details_poster: bool = False):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path.endswith("/configuration"):
            return httpx.Response(200, json={"images": {"secure_base_url": "https://image.test/t/p/"}})
        if path.endswith("/find/tt0086659"):
            return httpx.Response(200, json={
                "movie_results": [],
                "tv_results": [{"id": 821, "name": "MacGyver", "poster_path": "/macgyver.jpg"}],
            })
        if path.endswith("/find/tt0111161"):
            return httpx.Response(200, json={
                "movie_results": [{"id": 278, "title": "The Shawshank Redemption",
                                   "poster_path": "/shawshank.jpg"}],
                "tv_results": [],
            })
        if empty and path.endswith(f"/find/{empty}"):
            return httpx.Response(200, json={"movie_results": [], "tv_results": []})
        if details_poster and "/tv/999" in path:
            return httpx.Response(200, json={"id": 999, "poster_path": "/details.jpg"})
        if request.url.host == "image.test":
            return httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/jpeg"})
        return httpx.Response(404)
    return handle


def _client(handler) -> httpx.Client:
    return httpx.Client(base_url="https://api.themoviedb.org/3",
                        transport=httpx.MockTransport(handler))


def test_fetch_movie_and_series(cfg):
    _add_movie(cfg, "tt0111161", "The Shawshank Redemption")
    con = connect(cfg)
    _show(con, "macgyver", "tt0086659")
    _show(con, "shawshank", "tt0111161")
    con.commit()
    calls: list[str] = []
    client = TmdbClient(cfg, client=_client(_handler(calls)))
    try:
        stats = fetch_posters(con, cfg, client=client, log=lambda _line: None)
    finally:
        client.close()

    assert stats["downloaded"] == 2
    assert stats["ok"] == 2 and stats["missing"] == 0 and stats["error"] == 0
    assert (cfg.posters_dir / "tt0086659.jpg").read_bytes() == b"image-bytes"
    assert (cfg.posters_dir / "tt0111161.jpg").exists()
    rows = {r["tconst"]: r for r in con.execute("SELECT * FROM posters")}
    assert rows["tt0086659"]["media_type"] == "tv"
    assert rows["tt0111161"]["media_type"] == "movie"
    cached = {r[0] for r in con.execute("SELECT cache_key FROM tmdb_cache")}
    assert "find:tt0086659:de-DE" in cached
    assert "find:tt0111161:de-DE" in cached
    assert "configuration" in cached
    con.close()


def test_missing_is_cached_and_not_requested_again(cfg):
    con = connect(cfg)
    _show(con, "missing", "tt0000000")
    con.commit()
    calls: list[str] = []
    client = TmdbClient(cfg, client=_client(_handler(calls, empty="tt0000000")))
    try:
        first = fetch_posters(con, cfg, client=client, log=lambda _line: None)
        second = fetch_posters(con, cfg, client=client, log=lambda _line: None)
    finally:
        client.close()
    con.close()

    assert first["missing"] == 1
    assert second["missing"] == 0 and second["skipped"] == 1
    assert sum(1 for path in calls if path.endswith("/find/tt0000000")) == 1


def test_poster_falls_back_to_details(cfg):
    db = sqlite3.connect(cfg.imdb_db)
    db.execute("INSERT INTO titles VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("tt9999999", "tvSeries", "Details Show", "Details Show", 2020, 2021, 45, "Drama", 7.0, 100))
    db.commit()
    db.close()
    con = connect(cfg)
    _show(con, "details", "tt9999999")
    con.commit()
    calls: list[str] = []

    def handler(request):
        path = request.url.path
        calls.append(path)
        if path.endswith("/configuration"):
            return httpx.Response(200, json={"images": {"secure_base_url": "https://image.test/t/p/"}})
        if path.endswith("/find/tt9999999"):
            return httpx.Response(200, json={
                "movie_results": [],
                "tv_results": [{"id": 999, "name": "Details Show", "poster_path": None}],
            })
        if "/tv/999" in path:
            return httpx.Response(200, json={"id": 999, "poster_path": "/details.jpg"})
        if request.url.host == "image.test":
            return httpx.Response(200, content=b"details-bytes", headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    client = TmdbClient(cfg, client=_client(handler))
    try:
        stats = fetch_posters(con, cfg, client=client, log=lambda _line: None)
    finally:
        client.close()
    assert stats["downloaded"] == 1
    cached = {r[0] for r in con.execute("SELECT cache_key FROM tmdb_cache")}
    assert "tv:999:de-DE" in cached
    con.close()


def test_auth_prefers_read_token(monkeypatch):
    monkeypatch.setenv("TVGUIDE_TMDB_READ_TOKEN", "read-token")
    monkeypatch.setenv("TVGUIDE_TMDB_API_KEY", "api-key")
    headers, params = _auth()
    assert headers == {"Authorization": "Bearer read-token"}
    assert params == {}


def test_auth_falls_back_to_api_key(monkeypatch):
    monkeypatch.delenv("TVGUIDE_TMDB_READ_TOKEN", raising=False)
    monkeypatch.setenv("TVGUIDE_TMDB_API_KEY", "api-key")
    headers, params = _auth()
    assert headers == {}
    assert params == {"api_key": "api-key"}


def test_auth_requires_credentials(monkeypatch):
    monkeypatch.delenv("TVGUIDE_TMDB_READ_TOKEN", raising=False)
    monkeypatch.delenv("TVGUIDE_TMDB_API_KEY", raising=False)
    with pytest.raises(PosterError):
        _auth()
