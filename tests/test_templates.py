"""Rendert die Templates direkt mit Jinja2 (unabhaengig von FastAPI)."""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from tvguide.db import connect
from tvguide.status import collect, fmt_num, fmt_size, fmt_ts

TEMPLATES = Path(__file__).resolve().parent.parent / "tvguide" / "web" / "templates"


def _env(tz="Europe/Berlin"):
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters["num"] = fmt_num
    env.filters["size"] = fmt_size
    env.filters["ts"] = lambda ts, fmt="%d.%m.%Y %H:%M": fmt_ts(ts, tz, fmt)
    return env


def test_status_page_renders(cfg):
    con = connect(cfg)
    data = collect(con, cfg)
    con.close()
    html = _env().get_template("status.html").render(active="status", s=data, version="x")
    assert "Status" in html
    assert "Sendungen" in html
    assert "Information courtesy of IMDb" in html
    assert "01.11.2026" in html


def test_status_page_with_problem(cfg):
    cfg.epg_db.unlink()
    con = connect(cfg)
    data = collect(con, cfg)
    con.close()
    html = _env().get_template("status.html").render(active="status", s=data, version="x")
    assert "Probleme" in html
    assert "epg.db fehlt" in html


def test_index_renders():
    html = _env().get_template("index.html").render(active="jetzt", version="x")
    assert "Jetzt" in html
