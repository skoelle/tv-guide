"""Read-only Diagnose-API fuer Live-Analysen (Review, Shows, Werke, SQL).

Die Funktionen hier werden nur von ``/api/support/...`` genutzt. Sie oeffnen
keine schreibenden Verbindungen und liefern bewusst mehr Rohdaten als die
normalen Web-Ansichten (Kandidaten-Signale, Roh-Titel, Sub-Titel, Laufzeiten).
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter
from statistics import median

from .categories import category as content_category
from .config import Config
from .marks import get_marks
from .matching import candidate_details, load_shows
from .status import collect

_SQL_FORBIDDEN = re.compile(
    r"\b(attach|detach|pragma|insert|update|delete|drop|alter|create|vacuum|reindex|analyze|replace)\b",
    re.I,
)


def _reason(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {"raw": str(raw)}
    return value if isinstance(value, dict) else {"value": value}


def _jsonable(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _samples(con: sqlite3.Connection, show_id: int, limit: int = 5) -> list[dict]:
    rows = con.execute(
        """SELECT COALESCE(c.name, es.channel_id) AS channel, es.channel_id,
                  e.start_ts, e.stop_ts, e.title, e.sub_title, e.description,
                  e.categories, e.prod_date, e.episode_num, e.rating
           FROM event_show es
           JOIN epg.events e ON e.channel_id = es.channel_id AND e.start_ts = es.start_ts
           LEFT JOIN epg.channels c ON c.id = es.channel_id
           WHERE es.show_id = ? ORDER BY e.start_ts LIMIT ?""", (show_id, limit)).fetchall()
    return [dict(r) for r in rows]


def _show_payload(con: sqlite3.Connection, cfg: Config, row, shows: dict, marks: dict) -> dict:
    show_key = row["show_key"]
    s = shows.get(show_key) or {
        "title": row["show_name"], "raw": Counter({row["show_name"]: 1}), "n": row["n"],
        "durs": [], "chs": Counter(), "cats": Counter(), "subs": set(), "desc": "",
        "yhint": None, "events": [],
    }
    durs = s.get("durs") or []
    tconst = row["tconst"]
    return {
        "show_id": row["show_id"],
        "show_key": show_key,
        "show_name": row["show_name"],
        "events": row["n"],
        "first_seen": row["first_seen"] if "first_seen" in row.keys() else None,
        "status": row["status"],
        "tconst": tconst,
        "score": row["score"],
        "method": row["method"],
        "reason": _reason(row["reason"]),
        "category": content_category(row["title_type"], row["genres"], marks.get(tconst)),
        "imdb": {
            "title": row["primary_title"],
            "year": row["start_year"],
            "type": row["title_type"],
            "genres": row["genres"],
        },
        "raw_titles": [{"title": k, "count": v} for k, v in s["raw"].most_common()],
        "sub_titles": sorted(s["subs"]),
        "durations": {
            "min": min(durs) if durs else None,
            "median": median(durs) if durs else None,
            "max": max(durs) if durs else None,
        },
        "channels": [{"name": k, "count": v} for k, v in s["chs"].most_common()],
        "categories": [{"name": k, "count": v} for k, v in s["cats"].most_common()],
        "description": s["desc"],
        "year_hint": s["yhint"],
        "suggestions": candidate_details(con, s, cfg, limit=10),
        "samples": _samples(con, row["show_id"], 5),
    }


def _review_rows(con: sqlite3.Connection) -> list:
    return con.execute(
        """SELECT s.id AS show_id, s.show_key, s.show_name, s.first_seen,
                  sm.tconst, sm.status, sm.score, sm.method, sm.reason,
                  count(es.channel_id) AS n,
                  t.title_type, t.genres, t.primary_title, t.start_year
           FROM show_match sm
           JOIN epg_shows s ON s.id = sm.show_id
           LEFT JOIN imdb.titles t ON t.tconst = sm.tconst
           LEFT JOIN event_show es ON es.show_id = s.id
           WHERE sm.status = 'review'
           GROUP BY s.id ORDER BY n DESC, s.show_name""").fetchall()


def review_entries(con: sqlite3.Connection, cfg: Config, limit: int = 50, offset: int = 0,
                   q: str = "") -> dict:
    shows = load_shows(con, cfg)[0]
    marks = get_marks(con)
    needle = q.strip().lower()
    rows = []
    for row in _review_rows(con):
        s = shows.get(row["show_key"])
        if content_category(row["title_type"], row["genres"], marks.get(row["tconst"])) == "format":
            continue
        if needle:
            haystack = " ".join([
                row["show_name"] or "", row["primary_title"] or "",
                s["title"] if s else "", s["desc"] if s else "",
                *([t for t in s["raw"]] if s else []),
                *([t for t in s["subs"]] if s else []),
            ]).lower()
            if needle not in haystack:
                continue
        rows.append(row)
    total = len(rows)
    page = rows[offset:offset + limit]
    return {"total": total, "offset": offset, "limit": limit,
            "entries": [_show_payload(con, cfg, row, shows, marks) for row in page]}


def show_detail(con: sqlite3.Connection, cfg: Config, show_key: str) -> dict | None:
    row = con.execute(
        """SELECT s.id AS show_id, s.show_key, s.show_name, s.first_seen,
                  sm.tconst, sm.status, sm.score, sm.method, sm.reason,
                  (SELECT count(*) FROM event_show es WHERE es.show_id = s.id) AS n,
                  t.title_type, t.genres, t.primary_title, t.start_year
           FROM epg_shows s
           LEFT JOIN show_match sm ON sm.show_id = s.id
           LEFT JOIN imdb.titles t ON t.tconst = sm.tconst
           WHERE s.show_key = ?""", (show_key,)).fetchone()
    if row is None:
        return None
    shows = load_shows(con, cfg)[0]
    return _show_payload(con, cfg, row, shows, get_marks(con))


def work_detail(con: sqlite3.Connection, cfg: Config, tconst: str) -> dict | None:
    title = con.execute(
        """SELECT tconst, primary_title, original_title, title_type, start_year, end_year,
                  runtime_min, genres, avg_rating, num_votes
           FROM imdb.titles WHERE tconst = ?""", (tconst,)).fetchone()
    rows = con.execute(
        """SELECT s.id AS show_id, s.show_key, s.show_name, s.first_seen,
                  sm.tconst, sm.status, sm.score, sm.method, sm.reason,
                  count(es.channel_id) AS n,
                  t.title_type, t.genres, t.primary_title, t.start_year
           FROM show_match sm
           JOIN epg_shows s ON s.id = sm.show_id
           LEFT JOIN imdb.titles t ON t.tconst = sm.tconst
           LEFT JOIN event_show es ON es.show_id = s.id
           WHERE sm.tconst = ?
           GROUP BY s.id ORDER BY n DESC, s.show_name""", (tconst,)).fetchall()
    if title is None and not rows:
        return None
    shows = load_shows(con, cfg)[0]
    marks = get_marks(con)
    show_ids = [row["show_id"] for row in rows]
    airings: list[dict] = []
    if show_ids:
        placeholders = ",".join("?" * len(show_ids))
        airings = [dict(r) for r in con.execute(
            f"""SELECT es.show_id, COALESCE(c.name, es.channel_id) AS channel,
                       es.channel_id, e.start_ts, e.stop_ts, e.title, e.sub_title,
                       e.description, e.categories
                FROM event_show es
                JOIN epg.events e ON e.channel_id = es.channel_id AND e.start_ts = es.start_ts
                LEFT JOIN epg.channels c ON c.id = es.channel_id
                WHERE es.show_id IN ({placeholders})
                ORDER BY e.start_ts""", show_ids).fetchall()]
    return {
        "tconst": tconst,
        "title": title["primary_title"] if title else None,
        "original_title": title["original_title"] if title else None,
        "type": title["title_type"] if title else None,
        "year": title["start_year"] if title else None,
        "end_year": title["end_year"] if title else None,
        "runtime": title["runtime_min"] if title else None,
        "genres": title["genres"] if title else None,
        "rating": title["avg_rating"] if title else None,
        "votes": title["num_votes"] if title else None,
        "shows": [_show_payload(con, cfg, row, shows, marks) for row in rows],
        "airings": airings,
    }


def overview(con: sqlite3.Connection, cfg: Config) -> dict:
    data = collect(con, cfg)
    data["support"] = {"sql_enabled": cfg.support_sql_enabled}
    return data


def run_sql(con: sqlite3.Connection, sql: str, limit: int = 1000, timeout_ms: int = 5000) -> dict:
    """Fuehrt eine einzelne read-only SELECT/WITH-Abfrage aus."""
    cleaned = sql.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if not cleaned:
        raise ValueError("empty SQL")
    if ";" in cleaned:
        raise ValueError("only one statement is allowed")
    first = cleaned.split(None, 1)[0].lower()
    if first not in ("select", "with"):
        raise ValueError("only SELECT/WITH is allowed")
    if _SQL_FORBIDDEN.search(cleaned):
        raise ValueError("statement contains a forbidden keyword")
    limit = max(1, min(int(limit), 5000))
    deadline = time.monotonic() + max(1, timeout_ms) / 1000.0

    def _progress() -> int:
        return 1 if time.monotonic() > deadline else 0

    con.set_progress_handler(_progress, 10000)
    try:
        cur = con.execute(cleaned)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit + 1)
    finally:
        con.set_progress_handler(None, 0)
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {
        "columns": columns,
        "rows": [{col: _jsonable(value) for col, value in zip(columns, row)} for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }
