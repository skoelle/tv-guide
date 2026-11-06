"""Rendert die Templates direkt mit Jinja2 (unabhaengig von FastAPI)."""
from dataclasses import replace
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from tvguide.browse import (annotate_next, catalog_facets, evening_airings, genre_de, load_catalog,
                            parse_filters, running_airings)
from tvguide.categories import label as category_label
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
    env.globals["category_label"] = category_label
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
        "retro_from": cfg.retro_from, "retro_to": cfg.retro_to,
    }
    con.close()
    html = _env().get_template("index.html").render(**ctx)
    assert "Jetzt" in html
    assert "Läuft gerade" in html
    assert "Retro" in html
    assert "nur zukünftige" not in html


def test_retro_hint_uses_config(cfg):
    cfg = replace(cfg, retro_from=1970, retro_to=1989)
    con = connect(cfg)
    works = load_catalog(con, cfg)
    f = parse_filters({})
    ctx = {
        "f": f, "facets": catalog_facets(works, f, cfg), "at": AT,
        "running": running_airings(works, AT, f, cfg), "evening": evening_airings(works, AT, f, cfg),
        "nav": _nav("jetzt"), "version": "x", "qs": _qs, "toggle": _toggle,
        "retro_from": cfg.retro_from, "retro_to": cfg.retro_to,
    }
    con.close()
    html = _env().get_template("index.html").render(**ctx)
    assert "1970–1989" in html
    assert "1980–1999" not in html


def test_browse_title_links_to_work(cfg):
    f = parse_filters({})
    work = {
        "tconst": "tt1", "title": "Werk", "year": 1985, "end_year": None,
        "kind": "film", "category": "film", "genres": ["Action"], "genres_de": ["Action"],
        "status": "auto", "poster": False,
        "airings": [{"channel": "Test", "channel_id": "c1", "start_ts": AT, "stop_ts": AT + 3600,
                     "sub_title": None, "title": "Werk"}],
    }
    works = annotate_next([work], AT)
    ctx = {
        "f": f, "facets": catalog_facets([], f, cfg), "at": AT, "works": works,
        "total": 1, "page": 1, "pages": 1, "nav": _nav("stoebern"), "version": "x",
        "qs": _qs, "toggle": _toggle,
        "retro_from": cfg.retro_from, "retro_to": cfg.retro_to,
    }
    html = _env().get_template("browse.html").render(**ctx)
    assert 'href="/work/tt1"' in html
    assert "nur zukünftige" in html
    assert 'name="upcoming"' in html
    assert "Genre ausschließen" in html
    assert 'name="genre_not"' in html
