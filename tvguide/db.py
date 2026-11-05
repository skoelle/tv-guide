"""Verbindung zu app.db (lesen/schreiben) mit epg.db und imdb.db read-only per ATTACH."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import Config

MIGRATIONS: list[str] = [
    # Version 1: Tabellen aus SPEC Abschnitt 4
    """
    CREATE TABLE epg_shows (
        id          INTEGER PRIMARY KEY,
        show_key    TEXT NOT NULL UNIQUE,
        show_name   TEXT NOT NULL,
        first_seen  TEXT NOT NULL
    );
    CREATE TABLE show_match (
        show_id     INTEGER PRIMARY KEY REFERENCES epg_shows(id),
        tconst      TEXT,
        status      TEXT NOT NULL,
        score       REAL,
        method      TEXT,
        reason      TEXT,
        updated_at  TEXT NOT NULL
    );
    CREATE INDEX idx_show_match_status ON show_match(status);
    CREATE TABLE event_show (
        channel_id   TEXT NOT NULL,
        start_ts     INTEGER NOT NULL,
        show_id      INTEGER NOT NULL REFERENCES epg_shows(id),
        episode_hint TEXT,
        PRIMARY KEY (channel_id, start_ts)
    );
    CREATE INDEX idx_event_show_show ON event_show(show_id);
    CREATE TABLE overrides (
        show_key    TEXT PRIMARY KEY,
        tconst      TEXT,
        note        TEXT,
        created_at  TEXT NOT NULL
    );
    CREATE TABLE saved_searches (
        id             INTEGER PRIMARY KEY,
        name           TEXT NOT NULL,
        filter_json    TEXT NOT NULL,
        include_review INTEGER NOT NULL DEFAULT 0,
        notify         INTEGER NOT NULL DEFAULT 0,
        created_at     TEXT NOT NULL,
        last_run_at    TEXT
    );
    CREATE TABLE search_hits (
        search_id   INTEGER NOT NULL REFERENCES saved_searches(id) ON DELETE CASCADE,
        channel_id  TEXT NOT NULL,
        start_ts    INTEGER NOT NULL,
        found_at    TEXT NOT NULL,
        notified_at TEXT,
        PRIMARY KEY (search_id, channel_id, start_ts)
    );
    """,
    # Version 2: interner Zustand der App (Refresh-Erkennung usw.)
    """
    CREATE TABLE app_meta (
        key    TEXT PRIMARY KEY,
        value  TEXT
    );
    """,
    # Version 3: Verlauf der Review-Entscheidungen (Rueckgaengig)
    """
    CREATE TABLE override_log (
        id                   INTEGER PRIMARY KEY,
        show_key             TEXT NOT NULL,
        created_at           TEXT NOT NULL,
        prev_override_exists INTEGER NOT NULL,
        prev_override_tconst TEXT,
        prev_status          TEXT,
        prev_tconst          TEXT,
        prev_score           REAL,
        prev_method          TEXT,
        prev_reason          TEXT,
        new_tconst           TEXT
    );
    """,
    # Version 4: Trefferzahl des letzten Suchlaufs
    """
    ALTER TABLE saved_searches ADD COLUMN last_new INTEGER NOT NULL DEFAULT 0;
    """,
]


class Db(sqlite3.Connection):
    """sqlite3.Connection mit Zusatzinfo, welche Datenbanken angehaengt sind."""

    attached: dict[str, str]  # Name -> ok | missing | immutable | error:<text>


def _uri(path: Path, query: str) -> str:
    return path.resolve().as_uri() + "?" + query


def attach_readonly(con: Db, name: str, path: Path) -> str:
    """Haengt eine Datenbank read-only an. Gibt den Zustand zurueck."""
    if not path.exists():
        return "missing"
    for query, state in (("mode=ro", "ok"), ("mode=ro&immutable=1", "immutable")):
        try:
            con.execute(f"ATTACH DATABASE ? AS {name}", (_uri(path, query),))
            con.execute(f"SELECT count(*) FROM {name}.sqlite_master").fetchone()
            return state
        except sqlite3.Error as exc:
            last = str(exc)
            try:
                con.execute(f"DETACH DATABASE {name}")
            except sqlite3.Error:
                pass
    return f"error: {last}"


def current_version(con: sqlite3.Connection) -> int:
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = con.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        con.execute("INSERT INTO schema_version (version) VALUES (0)")
        con.commit()
        return 0
    return int(row[0])


def migrate(con: sqlite3.Connection) -> int:
    """Wendet fehlende Migrationen an und gibt die aktuelle Version zurueck."""
    version = current_version(con)
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        con.executescript(script)
        con.execute("UPDATE schema_version SET version = ?", (number,))
        con.commit()
    return len(MIGRATIONS) if version < len(MIGRATIONS) else version


def connect(cfg: Config, *, run_migrations: bool = True) -> Db:
    """Oeffnet app.db (anlegen falls noetig) und haengt epg und imdb read-only an."""
    cfg.app_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_uri(cfg.app_db, "mode=rwc"), uri=True, factory=Db, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    if run_migrations:
        migrate(con)
    con.attached = {
        "epg": attach_readonly(con, "epg", cfg.epg_db),
        "imdb": attach_readonly(con, "imdb", cfg.imdb_db),
    }
    return con
