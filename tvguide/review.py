"""Review: offene Zuordnungen bestaetigen, ablehnen oder manuell zuordnen.

Entscheidungen landen in `overrides` (haben Vorrang) und werden protokolliert
(`override_log`), damit die letzten Entscheidungen rueckgaengig gemacht werden koennen.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .config import Config
from .matching import load_shows, suggestions


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def review_count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM show_match WHERE status = 'review'").fetchone()[0]


def show_key_for(con: sqlite3.Connection, ident: str) -> str | None:
    """Akzeptiert show_key oder numerische show_id."""
    if str(ident).isdigit():
        row = con.execute("SELECT show_key FROM epg_shows WHERE id = ?", (int(ident),)).fetchone()
        return row[0] if row else None
    row = con.execute("SELECT show_key FROM epg_shows WHERE show_key = ?", (ident,)).fetchone()
    return row[0] if row else None


def _samples(con: sqlite3.Connection, show_id: int, limit: int = 3) -> list[dict]:
    rows = con.execute(
        """SELECT COALESCE(c.name, es.channel_id) AS channel, e.start_ts, e.stop_ts, e.sub_title
           FROM event_show es
           JOIN epg.events e ON e.channel_id = es.channel_id AND e.start_ts = es.start_ts
           LEFT JOIN epg.channels c ON c.id = es.channel_id
           WHERE es.show_id = ? ORDER BY e.start_ts LIMIT ?""", (show_id, limit)).fetchall()
    return [dict(r) for r in rows]


def list_review(con: sqlite3.Connection, cfg: Config, limit: int = 25, offset: int = 0) -> list[dict]:
    rows = con.execute(
        """SELECT s.id, s.show_key, s.show_name, count(es.channel_id) AS n
           FROM show_match sm JOIN epg_shows s ON s.id = sm.show_id
           LEFT JOIN event_show es ON es.show_id = s.id
           WHERE sm.status = 'review'
           GROUP BY s.id ORDER BY n DESC, s.show_name LIMIT ? OFFSET ?""", (limit, offset)).fetchall()
    shows = load_shows(con, cfg)[0]
    out = []
    for r in rows:
        s = shows.get(r["show_key"]) or {
            "title": r["show_name"], "n": r["n"], "durs": [], "subs": set(), "desc": "", "yhint": None}
        out.append({
            "show_key": r["show_key"], "show_name": r["show_name"], "events": r["n"],
            "suggestions": suggestions(con, s, cfg, 5),
            "samples": _samples(con, r["id"], 3),
        })
    return out


def apply_override(con: sqlite3.Connection, show_key: str, tconst: str | None, note: str | None = None) -> None:
    """Setzt eine Entscheidung: tconst=None bedeutet 'kein Treffer' (rejected)."""
    show_row = con.execute("SELECT id FROM epg_shows WHERE show_key = ?", (show_key,)).fetchone()
    if show_row is None:
        raise KeyError(show_key)
    show_id = show_row[0]
    now = _now()
    prev_ov = con.execute("SELECT tconst FROM overrides WHERE show_key = ?", (show_key,)).fetchone()
    prev = con.execute("SELECT status, tconst, score, method, reason FROM show_match WHERE show_id = ?",
                       (show_id,)).fetchone()
    con.execute(
        """INSERT INTO override_log (show_key, created_at, prev_override_exists, prev_override_tconst,
             prev_status, prev_tconst, prev_score, prev_method, prev_reason, new_tconst)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (show_key, now, 1 if prev_ov else 0, prev_ov["tconst"] if prev_ov else None,
         prev["status"] if prev else None, prev["tconst"] if prev else None,
         prev["score"] if prev else None, prev["method"] if prev else None,
         prev["reason"] if prev else None, tconst))
    con.execute(
        """INSERT INTO overrides (show_key, tconst, note, created_at) VALUES (?,?,?,?)
           ON CONFLICT(show_key) DO UPDATE SET tconst = excluded.tconst, note = excluded.note,
             created_at = excluded.created_at""", (show_key, tconst, note, now))
    con.execute(
        """INSERT INTO show_match (show_id, tconst, status, score, method, reason, updated_at)
           VALUES (?,?,?,NULL,'override',NULL,?)
           ON CONFLICT(show_id) DO UPDATE SET tconst = excluded.tconst, status = excluded.status,
             score = NULL, method = 'override', reason = NULL, updated_at = excluded.updated_at""",
        (show_id, tconst, "confirmed" if tconst else "rejected", now))
    con.commit()


def undo_last(con: sqlite3.Connection) -> str | None:
    """Macht die letzte Entscheidung rueckgaengig. Gibt den show_key zurueck."""
    row = con.execute("SELECT * FROM override_log ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    show_key = row["show_key"]
    if row["prev_override_exists"]:
        con.execute(
            """INSERT INTO overrides (show_key, tconst, note, created_at) VALUES (?,?,NULL,?)
               ON CONFLICT(show_key) DO UPDATE SET tconst = excluded.tconst, created_at = excluded.created_at""",
            (show_key, row["prev_override_tconst"], _now()))
    else:
        con.execute("DELETE FROM overrides WHERE show_key = ?", (show_key,))
    show_row = con.execute("SELECT id FROM epg_shows WHERE show_key = ?", (show_key,)).fetchone()
    if show_row is not None:
        show_id = show_row[0]
        if row["prev_status"] is None:
            con.execute("DELETE FROM show_match WHERE show_id = ?", (show_id,))
        else:
            con.execute(
                """UPDATE show_match SET status = ?, tconst = ?, score = ?, method = ?, reason = ?,
                     updated_at = ? WHERE show_id = ?""",
                (row["prev_status"], row["prev_tconst"], row["prev_score"], row["prev_method"],
                 row["prev_reason"], _now(), show_id))
    con.execute("DELETE FROM override_log WHERE id = ?", (row["id"],))
    con.commit()
    return show_key
