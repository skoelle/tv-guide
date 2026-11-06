"""Rendert die Templates direkt mit Jinja2 (unabhaengig von FastAPI)."""
from dataclasses import replace
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from tvguide.browse import (airing_summary, annotate_next, catalog_facets, evening_airings, genre_de,
                            imdb_type_de, load_catalog, parse_filters, running_airings)
from tvguide.categories import label as category_label
from tvguide.db import connect
from tvguide.status import collect, fmt_num, fmt_size, fmt_ts
from tvguide.web.app import _airing_groups, _qs, _toggle

TEMPLATES = Path(__file__).resolve().parent.parent / "tvguide" / "web" / "templates"
AT = 1793538000  # 2026-11-01 13:00 UTC


def _env(tz="Europe/Berlin"):
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters["num"] = fmt_num
    env.filters["size"] = fmt_size
    env.filters["ts"] = lambda ts, fmt="%d.%m.%Y %H:%M": fmt_ts(ts, tz, fmt)
    env.globals["genre_de"] = genre_de
    env.globals["imdb_type_de"] = imdb_type_de
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


def test_work_shows_runtime_type_and_source_date(cfg):
    work = {
        "tconst": "tt0086659", "title": "Das A-Team", "imdb_title": "The A-Team",
        "original_title": None, "year": 1983, "end_year": 1987, "kind": "serie",
        "imdb_type": "tvSeries", "runtime": 60, "category": "serie",
        "genres": ["Action"], "genres_de": ["Action"], "status": "auto",
        "rating": 7.4, "votes": 90000, "poster": False, "tmdb_url": None,
        "airings": [],
    }
    ctx = {
        "w": work, "groups": [], "mark": None, "review_shows": [],
        "imdb_source_date": "2026-11-05", "nav": _nav(""), "version": "x",
        "qs": _qs, "toggle": _toggle,
    }
    html = _env().get_template("work.html").render(**ctx)
    assert "TV-Serie" in html
    assert "Folgenlänge" in html
    assert "60 Min." in html
    assert "IMDb-Datenstand" in html
    assert "2026-11-05" in html


def test_work_shows_tmdb_box(cfg):
    work = {
        "tconst": "tt0086659", "title": "Das A-Team", "imdb_title": "The A-Team",
        "original_title": None, "year": 1983, "end_year": 1987, "kind": "serie",
        "imdb_type": "tvSeries", "runtime": 60, "category": "serie",
        "genres": ["Action"], "genres_de": ["Action"], "status": "auto",
        "rating": 7.4, "votes": 90000, "poster": False, "tmdb_url": None,
        "airings": [],
    }
    tmdb_info = {
        "tmdb_id": 821, "media_type": "tv", "title": "Das A-Team",
        "original_title": "The A-Team", "overview": "Beschreibung",
        "date": "23.01.1983", "date_label": "Erstausstrahlung",
        "vote_average": 7.5, "vote_count": 1234, "popularity": 42.5,
        "original_language": "en", "origin_country": ["US"],
        "fetched_date": "06.11.2026", "url": "https://www.themoviedb.org/tv/821",
    }
    ctx = {
        "w": work, "groups": [], "mark": None, "review_shows": [],
        "imdb_source_date": "2026-11-05", "tmdb_info": tmdb_info,
        "nav": _nav(""), "version": "x", "qs": _qs, "toggle": _toggle,
    }
    html = _env().get_template("work.html").render(**ctx)
    assert "TMDB-Titel" in html
    assert "Beschreibung" in html
    assert "Erstausstrahlung" in html
    assert "TMDB-Rating" in html
    assert "Popularität" in html
    assert "Originalsprache" in html
    assert "Herkunftsland" in html
    assert "TMDB-ID" in html
    assert "TMDB-Datenstand" in html
    assert 'href="https://www.themoviedb.org/tv/821"' in html


def test_work_shows_epg_box_und_details(cfg):
    airing = {
        "channel": "Kabel Eins", "channel_id": "c1", "start_ts": AT,
        "stop_ts": AT + 3600, "sub_title": "Folge 1", "title": "Das A-Team",
        "description": "Ein Kommando hilft Menschen in Not.",
        "categories": ["Action", "Drama"], "fsk": "12", "prod_date": "1983",
        "episodes": ["Staffel 1, Folge 2"], "star_rating": "3/5",
        "credits": [{"label": "Regie", "names": ["John Doe"]}],
    }
    work = {
        "tconst": "tt0086659", "title": "Das A-Team", "imdb_title": "The A-Team",
        "original_title": None, "year": 1983, "end_year": 1987, "kind": "serie",
        "imdb_type": "tvSeries", "runtime": 60, "category": "serie",
        "genres": ["Action"], "genres_de": ["Action"], "status": "auto",
        "rating": 7.4, "votes": 90000, "poster": False, "tmdb_url": None,
        "airings": [airing],
    }
    ctx = {
        "w": work, "groups": [{"label": "So, 01.11.2026", "airings": [airing]}],
        "epg": airing_summary([airing]), "mark": None, "review_shows": [],
        "imdb_source_date": "2026-11-05", "tmdb_info": None,
        "nav": _nav(""), "version": "x", "qs": _qs, "toggle": _toggle,
    }
    html = _env().get_template("work.html").render(**ctx)
    assert "EPG · Ausstrahlungen" in html
    assert "Kabel Eins" in html
    assert "Kategorien" in html
    assert "Action, Drama" in html
    assert "FSK" in html and "12" in html
    assert "Produktionsjahr" in html
    assert "Staffel 1, Folge 2" in html
    assert "Regie" in html and "John Doe" in html
    assert "Ein Kommando hilft Menschen in Not." in html
    assert 'class="grid work-sources"' in html
    assert "<h2>IMDb</h2>" in html


def test_work_shows_cast_and_episodes(cfg):
    airing = {
        "channel": "Kabel Eins", "channel_id": "c1", "start_ts": AT,
        "stop_ts": AT + 3600, "sub_title": "Folge 2", "title": "Das A-Team",
        "description": "", "categories": [], "fsk": "", "prod_date": "",
        "episodes": ["Staffel 1, Folge 2"], "star_rating": "", "credits": [],
        "season": 1, "episode": 2,
    }
    work = {
        "tconst": "tt0086659", "title": "Das A-Team", "imdb_title": "The A-Team",
        "original_title": None, "year": 1983, "end_year": 1987, "kind": "serie",
        "imdb_type": "tvSeries", "runtime": 60, "category": "serie",
        "genres": ["Action"], "genres_de": ["Action"], "status": "auto",
        "rating": 7.4, "votes": 90000, "poster": False, "tmdb_url": None,
        "airings": [airing],
    }
    credits = {
        "directors": [{"nconst": "nm1", "name": "John Doe"}],
        "writers": [{"nconst": "nm2", "name": "Jane Smith"}],
        "cast": [{"nconst": "nm3", "name": "Actor One", "role": "Hannibal Smith"}],
    }
    episode = {"tconst": "tt9000002", "season": 1, "episode": 2,
               "primary_title": "Episode Two", "start_year": 1983,
               "avg_rating": 8.1, "num_votes": 1234}
    episodes = {"items": [episode], "shown": [episode], "total": 1, "limit": 200}
    ctx = {
        "w": work, "groups": [{"label": "So, 01.11.2026", "airings": [airing]}],
        "epg": airing_summary([airing]), "mark": None, "review_shows": [],
        "imdb_source_date": "2026-11-05", "tmdb_info": None,
        "credits": credits, "episodes": episodes,
        "airing_episodes": {("c1", AT): episode},
        "nav": _nav(""), "version": "x", "qs": _qs, "toggle": _toggle,
    }
    html = _env().get_template("work.html").render(**ctx)
    assert "Besetzung &amp; Stab" in html
    assert 'href="/person/nm1"' in html
    assert 'href="/person/nm2"' in html
    assert 'href="/person/nm3"' in html
    assert 'href="https://www.imdb.com/name/nm1/"' in html
    assert "John Doe" in html and "Jane Smith" in html
    assert "Actor One" in html and "Hannibal Smith" in html
    assert "Episodenliste anzeigen" in html
    assert "Episode Two" in html and "8.1" in html
    assert "1 von 1" in html


def test_airing_groups_trennt_vergangene():
    tz = ZoneInfo("Europe/Berlin")
    past = {"start_ts": AT - 7200, "stop_ts": AT - 3600}
    running = {"start_ts": AT - 600, "stop_ts": AT + 600}
    future = {"start_ts": AT + 3600, "stop_ts": AT + 7200}
    groups, past_groups = _airing_groups([past, running, future], AT, tz)
    assert [a["start_ts"] for g in groups for a in g["airings"]] == [AT - 600, AT + 3600]
    assert [a["start_ts"] for g in past_groups for a in g["airings"]] == [AT - 7200]


def test_work_klappt_vergangene_termine_ein(cfg):
    past = {
        "channel": "Kabel Eins", "channel_id": "c1", "start_ts": AT - 7200,
        "stop_ts": AT - 3600, "sub_title": "Alte Folge", "title": "Das A-Team",
    }
    future = {
        "channel": "Kabel Eins", "channel_id": "c1", "start_ts": AT + 3600,
        "stop_ts": AT + 7200, "sub_title": "Neue Folge", "title": "Das A-Team",
    }
    work = {
        "tconst": "tt0086659", "title": "Das A-Team", "imdb_title": "The A-Team",
        "original_title": None, "year": 1983, "end_year": 1987, "kind": "serie",
        "imdb_type": "tvSeries", "runtime": 60, "category": "serie",
        "genres": ["Action"], "genres_de": ["Action"], "status": "auto",
        "rating": 7.4, "votes": 90000, "poster": False, "tmdb_url": None,
        "airings": [past, future],
    }
    ctx = {
        "w": work,
        "groups": [{"label": "So, 01.11.2026", "airings": [future]}],
        "past_groups": [{"label": "So, 01.11.2026", "airings": [past]}],
        "past_count": 1, "epg": airing_summary([past, future]), "mark": None,
        "review_shows": [], "imdb_source_date": "2026-11-05", "tmdb_info": None,
        "nav": _nav(""), "version": "x", "qs": _qs, "toggle": _toggle,
    }
    html = _env().get_template("work.html").render(**ctx)
    assert 'class="airing-details past-airings"' in html
    assert "Vergangene Termine anzeigen (1)" in html
    assert "Alte Folge" in html
    assert "Neue Folge" in html
