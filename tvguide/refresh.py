"""Refresh: erkennt geaenderte EPG-/IMDb-Daten und laesst das Matching laufen.

Der Zustand liegt in `app_meta` (EPG-XML-SHA1 + IMDb-Datenstand/Parameter).
Dadurch ist ein unveraenderter Lauf billig: ohne Aenderung wird nicht neu gematcht.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .config import Config
from .matching import run_match
from .notify.digest import send_notifications
from .search import run_searches


def _scalar(con: sqlite3.Connection, sql: str, params: tuple = ()):
    try:
        row = con.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row is not None else None


def get_meta(con: sqlite3.Connection) -> dict[str, str]:
    try:
        return {r[0]: r[1] for r in con.execute("SELECT key, value FROM app_meta")}
    except sqlite3.Error:
        return {}


def set_meta(con: sqlite3.Connection, values: dict[str, str]) -> None:
    con.executemany(
        "INSERT INTO app_meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        values.items())
    con.commit()


_IMDB_KEYS = ("source_date", "types", "min_votes", "min_year", "with_episodes")


def signature(con: sqlite3.Connection, cfg: Config) -> dict[str, str]:
    """Inhaltliche Erkennungsmerkmale von EPG und IMDb.

    EPG: SHA1 des zuletzt importierten XMLTV. IMDb: Datenstand und Build-Parameter.
    Ein Re-Match passiert nur, wenn sich einer der Werte wirklich aendert.
    """
    epg_sha1 = _scalar(
        con, "SELECT xml_sha1 FROM epg.imports WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1")
    try:
        meta = {r[0]: r[1] for r in con.execute(
            "SELECT key, value FROM imdb.meta WHERE key IN (%s)" % ",".join("?" * len(_IMDB_KEYS)), _IMDB_KEYS)}
    except sqlite3.Error:
        meta = {}
    imdb_key = "|".join(str(meta.get(k, "")) for k in _IMDB_KEYS)
    return {"epg_sha1": str(epg_sha1 or ""), "imdb_key": imdb_key}


def _summary(con: sqlite3.Connection) -> dict:
    by_status = {r[0]: r[1] for r in con.execute("SELECT status, count(*) FROM show_match GROUP BY status")}
    return {
        "shows": _scalar(con, "SELECT count(*) FROM epg_shows"),
        "events": _scalar(con, "SELECT count(*) FROM event_show"),
        "by_status": by_status,
    }


def refresh(con: sqlite3.Connection, cfg: Config, *, force: bool = False, progress=None) -> dict:
    """Fuehrt bei geaenderter epg.db einen Match-Lauf aus. Gibt die Statistik zurueck."""
    sig = signature(con, cfg)
    meta = get_meta(con)
    changed = force or any(meta.get(k) != v for k, v in sig.items())
    if not changed:
        out = _summary(con)
        out["changed"] = False
        return out

    stats = run_match(con, cfg, progress=progress)
    stats["changed"] = True
    stats["new_events"] = _scalar(con, "SELECT events_new FROM epg.imports ORDER BY id DESC LIMIT 1") or 0
    stats["searches"] = run_searches(con, cfg)
    try:
        stats["mail"] = send_notifications(con, cfg)
    except Exception as exc:  # noqa: BLE001 - Refresh darf an der Benachrichtigung nicht scheitern
        stats["mail"] = {"error": str(exc)}
    set_meta(con, {**sig, "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return stats
