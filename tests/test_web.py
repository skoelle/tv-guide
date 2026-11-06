"""Web-Tests brauchen fastapi und httpx (nach ./setup.sh vorhanden)."""
import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from tvguide.db import connect  # noqa: E402
from tvguide.web.app import create_app  # noqa: E402

_URL_RE = re.compile(r'\b(?:href|src|action|formaction|hx-(?:get|post|put|delete|patch))="([^"]*)"')


def _relative_urls(html: str) -> list[str]:
    return [u for u in _URL_RE.findall(html)
            if u and not u.startswith(("/", "http://", "https://", "#", "?"))]


@pytest.fixture
def client(cfg):
    connect(cfg).close()
    return TestClient(create_app(cfg))


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200 and "Jetzt" in r.text


def test_status_page(client):
    r = client.get("/status")
    assert r.status_code == 200
    assert "Sendungen" in r.text


def test_api_status(client):
    r = client.get("/api/status")
    data = r.json()
    assert data["epg"]["events"] == 3
    assert data["imdb"]["titles"] == 1


def test_urls_sind_root_absolut(client):
    for path in ("/", "/browse", "/review", "/searches", "/status"):
        html = client.get(path).text
        assert _relative_urls(html) == [], path


def test_index_zeigt_jetzt(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Läuft gerade" in r.text
    assert "Heute Abend" in r.text


def test_browse_seite(client):
    r = client.get("/browse")
    assert r.status_code == 200
    assert "Stöbern" in r.text


def test_api_browse_leer(client):
    r = client.get("/api/browse")
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_api_now(client):
    r = client.get("/api/now")
    assert r.status_code == 200
    assert r.json()["running"] == []


def test_work_unbekannt_ist_404(client):
    assert client.get("/work/tt0000000").status_code == 404


def test_status_zeigt_sync_buttons(client):
    r = client.get("/status")
    assert "EPG jetzt importieren" in r.text
    assert "IMDb jetzt neu bauen" in r.text
    assert 'hx-post="/api/jobs/epg"' in r.text
    assert 'hx-post="/api/jobs/imdb"' in r.text


def test_jobs_api_und_partial(client):
    assert client.get("/api/jobs").json()["running"] is False
    assert client.get("/partials/jobs").status_code == 200


def test_job_api_startet(client, cfg, monkeypatch):
    from tvguide import jobs

    def fake_run(cfg, kind, log):
        log("stub")
        return "ok"

    monkeypatch.setattr(jobs, "run_kind", fake_run)
    r = client.post("/api/jobs/epg")
    assert r.status_code == 202
    assert r.json()["started"] is True
    con = connect(cfg)
    row = con.execute("SELECT kind, status FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    assert tuple(row) == ("epg", "running") or tuple(row) == ("epg", "ok")


def test_job_api_busy(client, cfg):
    con = connect(cfg)
    con.execute("INSERT INTO jobs (kind, status, started_at) VALUES ('epg','running','x')")
    con.commit()
    con.close()
    assert client.post("/api/jobs/imdb").status_code == 409


def test_job_api_lehnt_unbekannt_ab(client):
    assert client.post("/api/jobs/nope").status_code == 404
    assert client.post("/jobs/epg").status_code in (404, 405)


def test_poster_route(client, cfg):
    cfg.posters_dir.mkdir(parents=True, exist_ok=True)
    (cfg.posters_dir / "tt0086659.jpg").write_bytes(b"image-bytes")
    con = connect(cfg)
    con.execute(
        "INSERT INTO posters (tconst, tmdb_id, media_type, poster_path, image_file, status, fetched_at) "
        "VALUES ('tt0086659', 821, 'tv', '/x.jpg', 'tt0086659.jpg', 'ok', '2026-11-06T00:00:00Z')")
    con.commit()
    con.close()
    r = client.get("/poster/tt0086659")
    assert r.status_code == 200
    assert r.content == b"image-bytes"
    assert r.headers["content-type"] == "image/jpeg"
    assert client.get("/poster/nope").status_code == 404
