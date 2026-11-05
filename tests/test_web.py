"""Web-Tests brauchen fastapi und httpx (nach ./setup.sh vorhanden)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from tvguide.db import connect  # noqa: E402
from tvguide.web.app import create_app  # noqa: E402


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
