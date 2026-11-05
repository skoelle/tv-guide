from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from tvguide.browse import (annotate_next, catalog_facets, evening_airings, genre_de, matches,
                            next_ts, parse_filters, running_airings, sort_works)
from tvguide.categories import category as content_category

AT = 1793538000  # 2026-11-01 13:00 UTC / 14:00 Berlin


def _airing(channel, start, dur=3600):
    return {"channel": channel, "start_ts": start, "stop_ts": start + dur, "sub_title": None, "title": "x"}


def _w(tconst="tt1", title="Werk", year=1985, type_="movie", status="auto", genres=("Action",),
       retro="film", airings=(), category=None):
    kind = "film" if type_ in ("movie", "tvMovie") else "serie"
    cat = category or content_category(type_, ",".join(genres))
    return {"tconst": tconst, "title": title, "year": year, "end_year": None, "type": type_, "kind": kind,
            "category": cat, "genres": list(genres), "genres_de": [genre_de(g) for g in genres],
            "genres_raw": ",".join(genres), "rating": 7.0, "votes": 100, "status": status,
            "retro": retro, "airings": list(airings)}


def test_parse_filters_standard():
    f = parse_filters({})
    assert f["status"] == {"sicher", "unsicher"}
    assert f["category"] == {"film", "serie"}
    assert f["sort"] == "termin" and f["page"] == 1 and f["retro"] is False


def test_kategorie_filter_und_facetten(cfg):
    f = parse_filters({})
    film = _w(type_="movie")
    serie = _w(type_="tvSeries", genres=("Drama",))
    fmt = _w(type_="tvSeries", genres=("Documentary",))
    assert film["category"] == "film" and serie["category"] == "serie" and fmt["category"] == "format"
    assert matches(film, f, cfg) and matches(serie, f, cfg)
    assert not matches(fmt, f, cfg)
    assert matches(fmt, {**f, "category": {"format"}}, cfg)
    fac = catalog_facets([film, serie, fmt], f, cfg)
    assert fac["category"] == {"film": 1, "serie": 1, "format": 1}


def test_keep_mark_holt_format_zurueck(cfg):
    from tvguide.categories import category
    fmt = _w(type_="tvSeries", genres=("Documentary",))
    kept = _w(type_="tvSeries", genres=("Documentary",), category=category("tvSeries", "Documentary", "keep"))
    f = parse_filters({})
    assert not matches(fmt, f, cfg)
    assert matches(kept, f, cfg)


def test_parse_filters_werte():
    f = parse_filters({"type": "film,serie", "decade": "1980,1990", "retro": "1", "page": "3"})
    assert f["type"] == {"film", "serie"}
    assert f["decade"] == {1980, 1990}
    assert f["retro"] is True and f["page"] == 3


def test_matches_dimensionen(cfg):
    f = parse_filters({})
    film80 = _w(year=1985, type_="movie", genres=("Action",))
    serie90 = _w(year=1995, type_="tvSeries", genres=("Comedy",), retro="serie_abgeschlossen")
    assert matches(film80, f, cfg)
    assert not matches(film80, {**f, "type": {"serie"}}, cfg)
    assert not matches(film80, {**f, "decade": {1990}}, cfg)
    assert not matches(serie90, {**f, "genre": {"Action"}}, cfg)
    assert matches(serie90, {**f, "genre": {"Comedy"}}, cfg)


def test_matches_status_und_retro(cfg):
    f = parse_filters({})
    unsicher = _w(status="review")
    assert not matches(unsicher, {**f, "status": {"sicher"}}, cfg)
    assert matches(unsicher, {**f, "status": {"unsicher"}}, cfg)
    laufend = _w(retro="serie_laufend")
    assert not matches(laufend, {**f, "retro": True}, cfg)
    assert matches(_w(retro="film"), {**f, "retro": True}, cfg)


def test_matches_sendergruppe(cfg):
    cfg = replace(cfg, channel_groups={"retro": ["TELE 5"]})
    f = {**parse_filters({}), "group": "retro"}
    assert matches(_w(airings=[_airing("TELE 5", AT)]), f, cfg)
    assert not matches(_w(airings=[_airing("RTL", AT)]), f, cfg)


def test_facets_zaehlen(cfg):
    works = [_w("tt1", type_="movie"), _w("tt2", type_="tvSeries"), _w("tt3", type_="movie", status="review")]
    f = parse_filters({})
    fac = catalog_facets(works, f, cfg)
    assert fac["type"]["film"] == 2 and fac["type"]["serie"] == 1
    assert fac["status"]["sicher"] == 2 and fac["status"]["unsicher"] == 1


def test_sortierung_und_next():
    a = _w("tt1", title="B", year=1990, airings=[_airing("X", AT + 100)])
    b = _w("tt2", title="A", year=1980, airings=[_airing("Y", AT + 50)])
    f = parse_filters({})
    assert [w["title"] for w in sort_works([a, b], {**f, "sort": "titel"}, AT)] == ["A", "B"]
    assert [w["year"] for w in sort_works([a, b], {**f, "sort": "jahr"}, AT)] == [1980, 1990]
    assert next_ts(a, AT) == AT + 100


def test_annotate_next():
    w = _w(airings=[_airing("X", AT - 10), _airing("X", AT + 100), _airing("X", AT + 200)])
    annotate_next([w], AT)
    assert w["upcoming"] == 2
    assert w["next"]["start_ts"] == AT + 100


def test_running_und_evening(cfg):
    tz = ZoneInfo(cfg.timezone)
    evening = int(datetime.fromtimestamp(AT, tz).replace(hour=20, minute=30).timestamp())
    work = _w(airings=[_airing("X", AT - 600), _airing("X", evening)])
    f = parse_filters({})
    assert [x["airing"]["start_ts"] for x in running_airings([work], AT, f, cfg)] == [AT - 600]
    assert [x["airing"]["start_ts"] for x in evening_airings([work], AT, f, cfg)] == [evening]
