"""Manuelle Inhaltskategorien pro IMDb-Titel.

`mark = "format"` zwingt ein Werk in die Kategorie Format.
`mark = "keep"` holt ein automatisch als Format erkanntes Werk als Film/Serie zurueck.
Die letzte Markierung kann rueckgaengig gemacht werden.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

KEEP = "keep"
FORMAT = "format"
VALID = {KEEP, FORMAT}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_marks(con: sqlite3.Connection) -> dict[str, str]:
    try:
        return {r[0]: r[1] for r in con.execute("SELECT tconst, mark FROM content_marks")}
    except sqlite3.Error:
        return {}


def set_mark(con: sqlite3.Connection, tconst: str, mark: str) -> None:
    if mark not in VALID:
        raise ValueError(f"unknown mark: {mark}")
    con.execute(
        """INSERT INTO content_marks (tconst, mark, created_at) VALUES (?,?,?)
           ON CONFLICT(tconst) DO UPDATE SET mark = excluded.mark, created_at = excluded.created_at""",
        (tconst, mark, _now()))
    con.commit()


def delete_mark(con: sqlite3.Connection, tconst: str) -> None:
    con.execute("DELETE FROM content_marks WHERE tconst = ?", (tconst,))
    con.commit()


def undo_last(con: sqlite3.Connection) -> str | None:
    row = con.execute("SELECT id, tconst FROM content_marks ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    con.execute("DELETE FROM content_marks WHERE id = ?", (row[0],))
    con.commit()
    return row[1]


def recent(con: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = con.execute(
        """SELECT m.tconst, m.mark, m.created_at, t.primary_title, t.start_year, t.title_type
           FROM content_marks m LEFT JOIN imdb.titles t ON t.tconst = m.tconst
           ORDER BY m.id DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]
