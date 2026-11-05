"""Katalog fuer die Web-Ansichten: Werke, Termine, Filter, Facetten.

Liest app.db (event_show, show_match, epg_shows) zusammen mit epg.db und imdb.db
(per ATTACH) und baut daraus eine Liste von Werken (pro tconst).
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .categories import category as content_category
from .config import Config
from .marks import get_marks
from .matching import MOVIE_TYPES, retro_class

GENRES_DE = {
    "Action": "Action", "Adventure": "Abenteuer", "Animation": "Animation",
    "Biography": "Biografie", "Comedy": "Komödie", "Crime": "Krimi",
    "Documentary": "Doku", "Drama": "Drama", "Family": "Familie", "Fantasy": "Fantasy",
    "Film-Noir": "Film noir", "History": "Historie", "Horror": "Horror", "Music": "Musik",
    "Musical": "Musical", "Mystery": "Mystery", "News": "News", "Reality-TV": "Reality",
    "Romance": "Romanze", "Sci-Fi": "Sci-Fi", "Short": "Kurzfilm", "Sport": "Sport",
    "Talk-Show": "Talk", "Thriller": "Thriller", "War": "Krieg", "Western": "Western",
}

_CATALOG_SQL = """
SELECT es.channel_id, es.start_ts, s.show_key, s.show_name,
       sm.status, sm.tconst, sm.score,
       e.stop_ts, e.title, e.sub_title,
       COALESCE(c.name, es.channel_id) AS channel,
       t.title_type, t.start_year, t.end_year, t.genres, t.avg_rating, t.num_votes
FROM event_show es
JOIN epg_shows s ON s.id = es.show_id
JOIN show_match sm ON sm.show_id = es.show_id
JOIN epg.events e ON e.channel_id = es.channel_id AND e.start_ts = es.start_ts
LEFT JOIN epg.channels c ON c.id = es.channel_id
LEFT JOIN imdb.titles t ON t.tconst = sm.tconst
WHERE sm.tconst IS NOT NULL AND sm.status IN (%s)
"""


def genre_de(g: str) -> str:
    return GENRES_DE.get(g, g)


def _kind(title_type: str | None) -> str:
    return "film" if title_type in MOVIE_TYPES else "serie"


_CACHE: dict = {}


def _signature(con: sqlite3.Connection, cfg: Config, statuses: tuple[str, ...]):
    """Aendert sich, sobald eine der Datenbanken oder app.db-Inhalte neu sind."""
    def mt(p):
        return p.stat().st_mtime_ns if p.exists() else None
    sm = con.execute("SELECT max(updated_at), count(*) FROM show_match").fetchone()
    ev = con.execute("SELECT count(*) FROM event_show").fetchone()[0]
    try:
        marks = con.execute("SELECT max(id), count(*) FROM content_marks").fetchone()
    except sqlite3.Error:
        marks = (None, 0)
    return (statuses, sm[0], sm[1], ev, marks[0], marks[1],
            mt(cfg.epg_db), mt(cfg.imdb_db), mt(cfg.app_db))


def load_catalog(con: sqlite3.Connection, cfg: Config, statuses: tuple[str, ...] = ("auto", "confirmed", "review")) -> list[dict]:
    """Baut die Werk-Liste aus allen zugeordneten Shows/Terminen (mit Prozess-Cache)."""
    key = _signature(con, cfg, statuses)
    if _CACHE.get("key") == key:
        return _CACHE["works"]
    placeholders = ",".join("?" * len(statuses))
    rows = con.execute(_CATALOG_SQL % placeholders, statuses).fetchall()
    marks = get_marks(con)
    works: dict[str, dict] = {}
    for r in rows:
        tc = r["tconst"]
        w = works.get(tc)
        if w is None:
            genres = [g for g in (r["genres"] or "").split(",") if g]
            w = works[tc] = {
                "tconst": tc, "title": r["show_name"], "_titles": Counter(),
                "year": r["start_year"], "end_year": r["end_year"], "type": r["title_type"],
                "kind": _kind(r["title_type"]), "genres": genres, "genres_de": [genre_de(g) for g in genres],
                "genres_raw": r["genres"] or "", "rating": r["avg_rating"], "votes": r["num_votes"],
                "status": r["status"],
                "category": content_category(r["title_type"], r["genres"], marks.get(tc)),
                "airings": [],
                "retro": retro_class({"start_year": r["start_year"], "end_year": r["end_year"],
                                      "title_type": r["title_type"], "genres": r["genres"] or ""}, cfg),
            }
        w["_titles"][r["show_name"]] += 1
        if r["status"] == "review":
            w["status"] = "review"
        w["airings"].append({
            "channel": r["channel"], "channel_id": r["channel_id"], "start_ts": r["start_ts"],
            "stop_ts": r["stop_ts"], "sub_title": r["sub_title"], "title": r["title"],
        })
    for w in works.values():
        w["title"] = w["_titles"].most_common(1)[0][0]
        w["airings"].sort(key=lambda a: a["start_ts"])
        w.pop("_titles", None)
    result = list(works.values())
    _CACHE["key"] = key
    _CACHE["works"] = result
    return result


def parse_filters(params) -> dict:
    """Liest Filter aus Query-Parametern. Fehlende Werte = Standard."""
    def lst(name):
        raw = params.get(name)
        if raw is None:
            return None
        return {v for v in raw.split(",") if v}

    status = lst("status")
    if status is None:
        status = {"sicher", "unsicher"}
    cat = lst("category")
    if cat is None:
        cat = {"film", "serie"}
    return {
        "type": lst("type"),
        "category": cat,
        "decade": {int(d) for d in lst("decade")} if params.get("decade") else None,
        "group": params.get("group") or None,
        "genre": lst("genre"),
        "status": status,
        "retro": params.get("retro") in ("1", "true", "on"),
        "q": (params.get("q") or "").strip(),
        "sort": params.get("sort") or "termin",
        "page": max(1, int(params.get("page") or 1) if str(params.get("page") or "1").isdigit() else 1),
    }


def matches(w: dict, f: dict, cfg: Config) -> bool:
    if f.get("category") and w.get("category", "serie") not in f["category"]:
        return False
    if f["type"] and w["kind"] not in f["type"]:
        return False
    if f["decade"] and (w["year"] is None or (w["year"] // 10 * 10) not in f["decade"]):
        return False
    if f["status"]:
        s = "unsicher" if w["status"] == "review" else "sicher"
        if s not in f["status"]:
            return False
    if f["genre"] and not (set(w["genres"]) & f["genre"]):
        return False
    if f["retro"] and w["retro"] not in ("film", "serie_abgeschlossen"):
        return False
    if f["group"]:
        members = set(cfg.channel_groups.get(f["group"], []))
        if not any(a["channel"] in members for a in w["airings"]):
            return False
    if f["q"]:
        q = f["q"].lower()
        if q not in w["title"].lower() and not any(q in (a["sub_title"] or "").lower() for a in w["airings"]):
            return False
    return True


def apply_filters(works: list[dict], f: dict, cfg: Config) -> list[dict]:
    return [w for w in works if matches(w, f, cfg)]


def next_ts(w: dict, at: int) -> int:
    future = [a["start_ts"] for a in w["airings"] if a["start_ts"] >= at]
    return min(future) if future else w["airings"][0]["start_ts"]


def sort_works(works: list[dict], f: dict, at: int) -> list[dict]:
    if f["sort"] == "titel":
        return sorted(works, key=lambda w: w["title"].lower())
    if f["sort"] == "jahr":
        return sorted(works, key=lambda w: (w["year"] if w["year"] is not None else 9999, w["title"].lower()))
    if f["sort"] == "rating":
        return sorted(works, key=lambda w: (-(w["rating"] or 0), w["title"].lower()))
    return sorted(works, key=lambda w: (next_ts(w, at), w["title"].lower()))


def catalog_facets(works: list[dict], f: dict, cfg: Config) -> dict:
    """Zaehler je Filtrewert, berechnet ohne die jeweils eigene Dimension."""
    def count(overrides):
        ff = {**f, **overrides}
        return sum(1 for w in works if matches(w, ff, cfg))

    decades = sorted({w["year"] // 10 * 10 for w in works if w["year"]})
    genres = sorted({g for w in works for g in w["genres"]})
    return {
        "category": {v: count({"category": {v}}) for v in ("film", "serie", "format")},
        "type": {v: count({"type": {v}}) for v in ("film", "serie")},
        "decade": {d: count({"decade": {d}}) for d in decades},
        "genre": {g: count({"genre": {g}}) for g in genres},
        "status": {v: count({"status": {v}}) for v in ("sicher", "unsicher")},
        "group": {g: count({"group": g}) for g in cfg.channel_groups},
    }


def _day_bounds(at: int, cfg: Config) -> tuple[int, int]:
    tz = ZoneInfo(cfg.timezone)
    local = datetime.fromtimestamp(at, tz)
    day0 = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(day0.timestamp()), int((day0 + timedelta(days=1)).timestamp())


def running_airings(works: list[dict], at: int, f: dict, cfg: Config) -> list[dict]:
    out = []
    for w in works:
        if not matches(w, f, cfg):
            continue
        for a in w["airings"]:
            if a["start_ts"] <= at < a["stop_ts"]:
                out.append({"work": w, "airing": a})
    return sorted(out, key=lambda x: x["airing"]["start_ts"])


def evening_airings(works: list[dict], at: int, f: dict, cfg: Config) -> list[dict]:
    tz = ZoneInfo(cfg.timezone)
    day0, day1 = _day_bounds(at, cfg)
    evening_start = int(datetime.fromtimestamp(day0, tz).replace(hour=20, minute=0).timestamp())
    window_start = max(evening_start, day0)
    out = []
    for w in works:
        if not matches(w, f, cfg):
            continue
        for a in w["airings"]:
            if window_start <= a["start_ts"] < day1:
                out.append({"work": w, "airing": a})
    return sorted(out, key=lambda x: x["airing"]["start_ts"])


def annotate_next(works: list[dict], at: int) -> list[dict]:
    """Setzt pro Werk den naechsten Termin (ab `at`) und die Anzahl kommender Termine."""
    for w in works:
        upcoming = [a for a in w["airings"] if a["start_ts"] >= at]
        w["next"] = upcoming[0] if upcoming else (w["airings"][-1] if w["airings"] else None)
        w["upcoming"] = len(upcoming)
    return works


def find_work(works: list[dict], tconst: str) -> dict | None:
    for w in works:
        if w["tconst"] == tconst:
            return w
    return None
