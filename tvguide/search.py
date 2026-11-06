"""Gespeicherte Suchen: validiertes Filter-JSON, Auswertung und Treffer (search_hits).

Die Sicherheit kommt aus einer strikten Whitelist: nur bekannte Felder und Werte werden
akzeptiert, es gibt keine Auswertung von Nutzer-Code. Ausgewertet wird mit derselben
Engine wie die Filteransicht (`browse`), damit beide dieselben Treffer liefern.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote

from .browse import apply_filters, load_catalog
from .config import Config

LIST_KEYS = ("type", "genre", "genre_not")
VALID_TYPES = {"film", "serie"}
VALID_STATUS = {"sicher", "unsicher"}
VALID_CATEGORIES = {"film", "serie", "format"}
FILTER_KEYS = {"type", "category", "decade", "group", "genre", "genre_not", "retro", "upcoming", "q"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_list(value) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [x for x in value.split(",") if x]
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("Liste erwartet")
    return [str(x) for x in value] or None


def normalize_filter(data, cfg: Config) -> dict:
    """Validiert ein Filter-JSON und gibt die kanonische Form zurueck."""
    data = data or {}
    if not isinstance(data, dict):
        raise ValueError("Filter muss ein Objekt sein")
    unknown = set(data) - FILTER_KEYS
    if unknown:
        raise ValueError(f"unbekannte Filterfelder: {', '.join(sorted(unknown))}")

    types = _as_list(data.get("type"))
    if types:
        bad = set(types) - VALID_TYPES
        if bad:
            raise ValueError(f"unbekannter Typ: {', '.join(sorted(bad))}")

    categories = _as_list(data.get("category"))
    if categories:
        bad = set(categories) - VALID_CATEGORIES
        if bad:
            raise ValueError(f"unbekannte Kategorie: {', '.join(sorted(bad))}")

    decades = None
    if data.get("decade"):
        raw = data["decade"]
        if isinstance(raw, str):
            raw = [x for x in raw.split(",") if x]
        try:
            decades = sorted({int(x) for x in raw})
        except (TypeError, ValueError):
            raise ValueError("decade muss Jahreszahlen enthalten")

    group = data.get("group") or None
    if group is not None and group not in cfg.channel_groups:
        raise ValueError(f"unbekannte Sendergruppe: {group}")

    return {
        "type": sorted(types) if types else None,
        "category": sorted(categories) if categories else None,
        "decade": decades,
        "group": group,
        "genre": sorted(_as_list(data.get("genre"))) if data.get("genre") else None,
        "genre_not": sorted(_as_list(data.get("genre_not"))) if data.get("genre_not") else None,
        "retro": bool(data.get("retro")),
        "upcoming": bool(data.get("upcoming")),
        "q": (data.get("q") or "").strip(),
    }


def effective_filters(filter_json: dict, include_review: bool) -> dict:
    """Wandelt das gespeicherte Filter-JSON in das Format der Browse-Engine um."""
    status = {"sicher", "unsicher"} if include_review else {"sicher"}
    return {
        "type": set(filter_json["type"]) if filter_json.get("type") else None,
        "category": set(filter_json["category"]) if filter_json.get("category") else {"film", "serie"},
        "decade": set(filter_json["decade"]) if filter_json.get("decade") else None,
        "group": filter_json.get("group"),
        "genre": set(filter_json["genre"]) if filter_json.get("genre") else None,
        "genre_not": set(filter_json["genre_not"]) if filter_json.get("genre_not") else None,
        "status": status,
        "retro": bool(filter_json.get("retro")),
        "upcoming": bool(filter_json.get("upcoming")),
        "q": filter_json.get("q") or "",
        "sort": "termin",
        "page": 1,
    }


def filter_to_query(filter_json: dict) -> str:
    """Query-String fuer /browse, passend zur Filteransicht."""
    parts: list[str] = []
    if filter_json.get("category") and set(filter_json["category"]) != {"film", "serie"}:
        parts.append("category=" + ",".join(sorted(filter_json["category"])))
    if filter_json.get("type"):
        parts.append("type=" + ",".join(sorted(filter_json["type"])))
    if filter_json.get("decade"):
        parts.append("decade=" + ",".join(str(d) for d in sorted(filter_json["decade"])))
    if filter_json.get("group"):
        parts.append("group=" + filter_json["group"])
    if filter_json.get("genre"):
        parts.append("genre=" + ",".join(sorted(filter_json["genre"])))
    if filter_json.get("genre_not"):
        parts.append("genre_not=" + ",".join(sorted(filter_json["genre_not"])))
    if filter_json.get("retro"):
        parts.append("retro=1")
    if filter_json.get("upcoming"):
        parts.append("upcoming=1")
    if filter_json.get("q"):
        parts.append("q=" + quote(filter_json["q"]))
    return "&".join(parts)


def describe(filter_json: dict, include_review: bool) -> str:
    """Kurzbeschreibung fuer die Uebersicht."""
    bits = []
    if filter_json.get("category"):
        names = {"film": "Film", "serie": "Serie", "format": "Format"}
        bits.append("+".join(names.get(c, c) for c in filter_json["category"]))
    if filter_json.get("type"):
        bits.append("+".join("Film" if t == "film" else "Serie" for t in filter_json["type"]))
    if filter_json.get("decade"):
        bits.append(", ".join(f"{d}er" for d in filter_json["decade"]))
    if filter_json.get("group"):
        bits.append(f"Gruppe {filter_json['group'].capitalize()}")
    if filter_json.get("genre"):
        bits.append(", ".join(filter_json["genre"]))
    if filter_json.get("genre_not"):
        bits.append("ohne " + ", ".join(filter_json["genre_not"]))
    if filter_json.get("retro"):
        bits.append("Retro")
    if filter_json.get("upcoming"):
        bits.append("nur zukünftige Termine")
    if filter_json.get("q"):
        bits.append(f'„{filter_json["q"]}"')
    bits.append("auch unsichere Treffer" if include_review else "nur sichere Treffer")
    return " · ".join(bits)


def matched_works(con: sqlite3.Connection, cfg: Config, filter_json: dict, include_review: bool,
                  at: int | None = None) -> list[dict]:
    at = at if at is not None else int(datetime.now(timezone.utc).timestamp())
    return apply_filters(load_catalog(con, cfg), effective_filters(filter_json, include_review), cfg, at)


def _row_filter(row) -> dict:
    return json.loads(row["filter_json"])


def create_search(con: sqlite3.Connection, cfg: Config, name: str, filter_json, include_review=False,
                  notify=False) -> int:
    fj = normalize_filter(filter_json, cfg)
    cur = con.execute(
        "INSERT INTO saved_searches (name, filter_json, include_review, notify, created_at) VALUES (?,?,?,?,?)",
        (name.strip() or "Suche", json.dumps(fj, ensure_ascii=False), 1 if include_review else 0,
         1 if notify else 0, _now()))
    con.commit()
    return cur.lastrowid


def update_search(con: sqlite3.Connection, search_id: int, *, name=None, include_review=None, notify=None) -> None:
    sets, params = [], []
    if name is not None:
        sets.append("name=?")
        params.append(name.strip() or "Suche")
    if include_review is not None:
        sets.append("include_review=?")
        params.append(1 if include_review else 0)
    if notify is not None:
        sets.append("notify=?")
        params.append(1 if notify else 0)
    if not sets:
        return
    params.append(search_id)
    con.execute(f"UPDATE saved_searches SET {', '.join(sets)} WHERE id=?", params)
    con.commit()


def delete_search(con: sqlite3.Connection, search_id: int) -> None:
    con.execute("DELETE FROM search_hits WHERE search_id=?", (search_id,))
    con.execute("DELETE FROM saved_searches WHERE id=?", (search_id,))
    con.commit()


def list_searches(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute("SELECT * FROM saved_searches ORDER BY name").fetchall()
    out = []
    for r in rows:
        hits = con.execute("SELECT count(*) FROM search_hits WHERE search_id=?", (r["id"],)).fetchone()[0]
        out.append({
            "id": r["id"], "name": r["name"], "filter": _row_filter(r),
            "include_review": bool(r["include_review"]), "notify": bool(r["notify"]),
            "hits": hits, "new": r["last_new"], "last_run_at": r["last_run_at"],
            "query": filter_to_query(_row_filter(r)),
            "summary": describe(_row_filter(r), bool(r["include_review"])),
        })
    return out


def get_search(con: sqlite3.Connection, search_id: int) -> dict | None:
    r = con.execute("SELECT * FROM saved_searches WHERE id=?", (search_id,)).fetchone()
    if r is None:
        return None
    return {"id": r["id"], "name": r["name"], "filter": _row_filter(r),
            "include_review": bool(r["include_review"]), "notify": bool(r["notify"]),
            "last_run_at": r["last_run_at"], "last_new": r["last_new"],
            "query": filter_to_query(_row_filter(r)),
            "summary": describe(_row_filter(r), bool(r["include_review"]))}


def hits_for(con: sqlite3.Connection, search_id: int, limit: int = 200) -> list[dict]:
    rows = con.execute(
        """SELECT sh.channel_id, sh.start_ts, sh.found_at, sh.notified_at,
                  COALESCE(c.name, sh.channel_id) AS channel, e.stop_ts, e.title, e.sub_title,
                  sm.tconst
           FROM search_hits sh
           LEFT JOIN epg.events e ON e.channel_id = sh.channel_id AND e.start_ts = sh.start_ts
           LEFT JOIN epg.channels c ON c.id = sh.channel_id
           LEFT JOIN event_show es ON es.channel_id = sh.channel_id AND es.start_ts = sh.start_ts
           LEFT JOIN show_match sm ON sm.show_id = es.show_id
           WHERE sh.search_id=? ORDER BY sh.start_ts LIMIT ?""", (search_id, limit)).fetchall()
    return [dict(r) for r in rows]


def run_searches(con: sqlite3.Connection, cfg: Config) -> list[dict]:
    """Schreibt fuer jede gespeicherte Suche die Treffer in `search_hits` fort.

    Nicht mehr passende Treffer (z. B. von neu ignorierten Sendern) werden entfernt.
    """
    rows = con.execute("SELECT * FROM saved_searches ORDER BY id").fetchall()
    con.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _current_hits ("
        "search_id INTEGER NOT NULL, channel_id TEXT NOT NULL, start_ts INTEGER NOT NULL, "
        "PRIMARY KEY (search_id, channel_id, start_ts))")
    con.execute("DELETE FROM _current_hits")
    stats = []
    for r in rows:
        works = matched_works(con, cfg, _row_filter(r), bool(r["include_review"]))
        pairs = {(a["channel_id"], a["start_ts"]) for w in works for a in w["airings"]}
        now = _now()
        con.executemany(
            "INSERT OR IGNORE INTO _current_hits (search_id, channel_id, start_ts) VALUES (?,?,?)",
            [(r["id"], ch, ts) for ch, ts in pairs])
        before = con.total_changes
        con.executemany(
            "INSERT OR IGNORE INTO search_hits (search_id, channel_id, start_ts, found_at) VALUES (?,?,?,?)",
            [(r["id"], ch, ts, now) for ch, ts in pairs])
        new = con.total_changes - before
        con.execute(
            "DELETE FROM search_hits WHERE search_id = ? AND NOT EXISTS ("
            "SELECT 1 FROM _current_hits c WHERE c.search_id = search_hits.search_id "
            "AND c.channel_id = search_hits.channel_id AND c.start_ts = search_hits.start_ts)",
            (r["id"],))
        total = con.execute("SELECT count(*) FROM search_hits WHERE search_id=?", (r["id"],)).fetchone()[0]
        con.execute("UPDATE saved_searches SET last_run_at=?, last_new=? WHERE id=?", (now, new, r["id"]))
        stats.append({"id": r["id"], "name": r["name"], "hits": total, "new": new})
    con.commit()
    return stats
