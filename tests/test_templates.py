"""Rendert die Templates direkt mit Jinja2 (unabhaengig von FastAPI)."""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from tvguide.browse import (catalog_facets, evening_airings, genre_de, load_catalog, parse_filters,
                            running_airings)
from tvguide.db import connect
from tvguide.status import collect, fmt_num, fmt_size, fmt_ts
from tvguide.web.app import _qs, _toggle

TEMPLATES = Path(__file__).resolve().parent.parent / "tvguide" / "web" / "templates"
AT = 1793538000  # 2026-11-01 13:00 UTC


def _env(tz="Europe/Berlin"):
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters["num"] = fmt_num
    env.filters["size"] = fmt_size
    env.filters["ts"] = lambda ts, fmt="%d.%m.%Y %H:%M": fmt_ts(ts, tz, fmt)
    env.globals["genre_de"] = genre_de
    env.globals["decade_label"] = lambda d: f"{d}er" if d >= 2000 else f"{str(d)[2:]}er"
    env.globals["with_sort"] = lambda f, v: {**f, "sort": v}
    env.globals["with_page"] = lambda f, p: {**f, "page": p}
    return env


def _nav(active):
    return {"active": active, "review": 0, "searches": 0}


def test_status_page_renders(cfg):
    con = connect(cfg)
    data = collect(con, cfg)
    con.close()
    html = _env().get_template("status.html").render(active="status", s=data, version="x", nav=_nav("status"))
    assert "Status" in html
    assert "Sendungen" in html
    assert "Information courtesy of IMDb" in html
    assert "01.11.2026" in html


def test_status_page_with_problem(cfg):
    cfg.epg_db.unlink()
    con = connect(cfg)
    data = collect(con, cfg)
    con.close()
    html = _env().get_template("status.html").render(active="status", s=data, version="x", nav=_nav("status"))
    assert "Probleme" in html
    assert "epg.db fehlt" in html


def test_index_renders(cfg):
    con = connect(cfg)
    works = load_catalog(con, cfg)
    f = parse_filters({})
    ctx = {
        "f": f, "facets": catalog_facets(works, f, cfg), "at": AT,
        "running": running_airings(works, AT, f, cfg), "evening": evening_airings(works, AT, f, cfg),
        "nav": _nav("jetzt"), "version": "x", "qs": _qs, "toggle": _toggle,
    }
    con.close()
    html = _env().get_template("index.html").render(**ctx)
    assert "Jetzt" in html
    assert "Läuft gerade" in html
    assert "Retro" in html
