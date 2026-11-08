"""Review: offene Zuordnungen bestaetigen, ablehnen oder manuell zuordnen.

Entscheidungen landen in `overrides` (haben Vorrang) und werden protokolliert
(`override_log`), damit die letzten Entscheidungen rueckgaengig gemacht werden koennen.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from .categories import category as content_category
from .config import Config
from .marks import FORMAT, get_marks, set_mark
from .matching import load_shows, suggestions


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _review_rows(con: sqlite3.Connection) -> list:
    try:
        rows = con.execute(
            """SELECT s.id, s.show_key, s.show_name, count(es.channel_id) AS n,
                      sm.tconst, sm.score, sm.method, sm.reason,
                      t.title_type, t.genres, t.primary_title, t.start_year
               FROM show_match sm JOIN epg_shows s ON s.id = sm.show_id
               LEFT JOIN imdb.titles t ON t.tconst = sm.tconst
               LEFT JOIN event_show es ON es.show_id = s.id
               WHERE sm.status = 'review'
               GROUP BY s.id ORDER BY n DESC, s.show_name""").fetchall()
    except sqlite3.Error:
        # imdb.db fehlt: ohne Genres bewerten (bleibt als "serie" sichtbar)
        rows = con.execute(
            """SELECT s.id, s.show_key, s.show_name, count(es.channel_id) AS n,
                      sm.tconst, sm.score, sm.method, sm.reason,
                      NULL AS title_type, NULL AS genres, NULL AS primary_title, NULL AS start_year
               FROM show_match sm JOIN epg_shows s ON s.id = sm.show_id
               LEFT JOIN event_show es ON es.show_id = s.id
               WHERE sm.status = 'review'
               GROUP BY s.id ORDER BY n DESC, s.show_name""").fetchall()
    marks = get_marks(con)
    return [r for r in rows
            if content_category(r["title_type"], r["genres"], marks.get(r["tconst"])) != "format"]


def review_count(con: sqlite3.Connection) -> int:
    """Anzahl offener Review-Shows (nicht Gruppen)."""
    return len(_review_rows(con))


def review_group_count(con: sqlite3.Connection, cfg: Config | None = None) -> int:
    """Anzahl offener Review-Gruppen (pro Werk/tconst)."""
    return len({r["tconst"] or r["show_key"] for r in _review_rows(con)})


def _show_stub(row) -> dict:
    return {"show_id": row["id"], "show_key": row["show_key"], "show_name": row["show_name"],
            "events": row["n"], "score": row["score"], "method": row["method"],
            "reason": row["reason"]}


def review_groups(con: sqlite3.Connection, cfg: Config, limit: int | None = None,
                  offset: int = 0) -> list[dict]:
    """Offene Reviews pro Werk gruppieren.

    Mehrere EPG-Shows mit demselben Kandidaten (z. B. alle Tatort-Folgen) werden
    zu einer Gruppe mit einem Bestaetigen/Ablehnen zusammenfasst.
    """
    rows = _review_rows(con)
    if not rows:
        return []
    shows = load_shows(con, cfg)[0]
    groups: dict[str, dict] = {}
    for r in rows:
        key = r["tconst"] or r["show_key"]
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "tconst": r["tconst"], "title": r["primary_title"] or r["show_name"],
                "year": r["start_year"], "type": r["title_type"], "genres": r["genres"],
                "events": 0, "shows": [], "samples": [], "suggestions": [],
            }
        g["events"] += r["n"]
        g["shows"].append(_show_stub(r))
    for g in groups.values():
        g["shows"].sort(key=lambda x: (-x["events"], x["show_name"]))
        rep = g["shows"][0]
        s = shows.get(rep["show_key"])
        if s is not None:
            g["suggestions"] = suggestions(con, s, cfg, 5)
        g["samples"] = _samples(con, rep["show_id"], 3)
        g["show_count"] = len(g["shows"])
        g["show_names"] = [x["show_name"] for x in g["shows"]]
        g["show_keys"] = [x["show_key"] for x in g["shows"]]
    ordered = sorted(groups.values(), key=lambda g: (g["title"] or "").casefold())
    if limit is None:
        return ordered[offset:]
    return ordered[offset:offset + limit]


def mark_as_format(con: sqlite3.Connection, show_key: str) -> str | None:
    row = con.execute(
        """SELECT sm.tconst FROM show_match sm JOIN epg_shows s ON s.id = sm.show_id
           WHERE s.show_key = ?""", (show_key,)).fetchone()
    if row is None or not row[0]:
        return None
    set_mark(con, row[0], FORMAT)
    return row[0]


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


def review_entries_for_tconst(con: sqlite3.Connection, cfg: Config, tconst: str) -> list[dict]:
    """Offene Review-Shows eines Werks inklusive Kandidaten (fuer die Werkseite)."""
    rows = con.execute(
        """SELECT s.show_key, s.show_name
           FROM show_match sm JOIN epg_shows s ON s.id = sm.show_id
           WHERE sm.tconst = ? AND sm.status = 'review'
           ORDER BY s.show_name""", (tconst,)).fetchall()
    if not rows:
        return []
    shows = load_shows(con, cfg)[0]
    out = []
    for r in rows:
        s = shows.get(r["show_key"]) or {
            "title": r["show_name"], "n": 0, "durs": [], "subs": set(), "desc": "", "yhint": None}
        out.append({"show_key": r["show_key"], "show_name": r["show_name"],
                    "suggestions": suggestions(con, s, cfg, 5)})
    return out


def list_review(con: sqlite3.Connection, cfg: Config, limit: int = 25, offset: int = 0) -> list[dict]:
    rows = _review_rows(con)[offset:offset + limit]
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


def _apply_override(con: sqlite3.Connection, show_key: str, tconst: str | None,
                    note: str | None, batch_id: str) -> None:
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
             prev_status, prev_tconst, prev_score, prev_method, prev_reason, new_tconst, batch_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (show_key, now, 1 if prev_ov else 0, prev_ov["tconst"] if prev_ov else None,
         prev["status"] if prev else None, prev["tconst"] if prev else None,
         prev["score"] if prev else None, prev["method"] if prev else None,
         prev["reason"] if prev else None, tconst, batch_id))
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


def apply_override(con: sqlite3.Connection, show_key: str, tconst: str | None, note: str | None = None) -> None:
    """Setzt eine Entscheidung: tconst=None bedeutet 'kein Treffer' (rejected)."""
    _apply_override(con, show_key, tconst, note, uuid.uuid4().hex)
    con.commit()


def apply_overrides(con: sqlite3.Connection, show_keys: list[str], tconst: str | None,
                    note: str | None = None) -> int:
    """Setzt dieselbe Entscheidung fuer mehrere Shows (Review-Gruppe)."""
    batch_id = uuid.uuid4().hex
    applied = 0
    for show_key in show_keys:
        try:
            _apply_override(con, show_key, tconst, note, batch_id)
            applied += 1
        except KeyError:
            continue
    con.commit()
    return applied


def reopen_work(con: sqlite3.Connection, tconst: str) -> int:
    """Loest die Zuordnung eines Werks und legt alle Shows zurueck ins Review.

    Die Overrides werden entfernt, damit ein spaeterer Refresh die alte
    Zuordnung nicht wiederherstellt. Gibt die Anzahl betroffener Shows zurueck.
    """
    rows = con.execute(
        """SELECT s.show_key, sm.show_id FROM show_match sm
           JOIN epg_shows s ON s.id = sm.show_id
           WHERE sm.tconst = ?""", (tconst,)).fetchall()
    if not rows:
        return 0
    now = _now()
    for row in rows:
        con.execute("DELETE FROM overrides WHERE show_key = ?", (row["show_key"],))
        con.execute(
            """UPDATE show_match SET tconst = NULL, status = 'review', score = NULL,
                 method = 'reopen', reason = NULL, updated_at = ? WHERE show_id = ?""",
            (now, row["show_id"]))
    con.commit()
    return len(rows)


def _revert_log_row(con: sqlite3.Connection, row) -> str:
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
    return show_key


def undo_last(con: sqlite3.Connection) -> str | None:
    """Macht die letzte Entscheidung bzw. Review-Gruppe rueckgaengig."""
    row = con.execute("SELECT * FROM override_log ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    batch_id = row["batch_id"] if "batch_id" in row.keys() else None
    if batch_id:
        rows = con.execute("SELECT * FROM override_log WHERE batch_id = ? ORDER BY id DESC",
                           (batch_id,)).fetchall()
    else:
        rows = [row]
    show_key = row["show_key"]
    for entry in rows:
        _revert_log_row(con, entry)
    con.commit()
    return show_key
