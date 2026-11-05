import sqlite3
from pathlib import Path

import pytest

from tvguide.config import DEFAULT_SKIP_CATEGORIES, DEFAULT_SKIP_TITLES, Config


def make_epg_db(path: Path, events: int = 3) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE channels (id TEXT PRIMARY KEY, name TEXT NOT NULL, number TEXT, icon TEXT, last_seen TEXT NOT NULL);
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL, start_ts INTEGER NOT NULL,
            stop_ts INTEGER NOT NULL, start_local TEXT NOT NULL, title TEXT NOT NULL, sub_title TEXT,
            description TEXT, categories TEXT, rating TEXT, prod_date TEXT, episode_num TEXT, credits TEXT,
            star_rating TEXT, icon TEXT, content_hash TEXT NOT NULL, first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL, updated_at TEXT NOT NULL, import_id INTEGER, UNIQUE (channel_id, start_ts));
        CREATE TABLE imports (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
            source TEXT, xml_sha1 TEXT, xml_bytes INTEGER, channels INTEGER, events_total INTEGER,
            events_new INTEGER, events_changed INTEGER, events_pruned INTEGER);
    """)
    con.execute("INSERT INTO channels VALUES ('c1','Kabel Eins','5',NULL,'2026-11-05T00:00:00Z')")
    con.execute("INSERT INTO channels VALUES ('c2','TELE 5','6',NULL,'2026-11-05T00:00:00Z')")
    base = 1793538000  # 2026-11-01 13:00 UTC
    for i in range(events):
        con.execute(
            "INSERT INTO events (channel_id,start_ts,stop_ts,start_local,title,content_hash,first_seen,last_seen,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            ("c1", base + i * 3600, base + (i + 1) * 3600, "x", f"Sendung {i}", "h", "t", "t", "t"),
        )
    con.execute("INSERT INTO imports (started_at, finished_at, source, events_total, events_new, events_changed)"
                " VALUES ('2026-11-05T17:50:00Z','2026-11-05T17:50:30Z','test',%d,%d,0)" % (events, events))
    con.commit()
    con.close()


def make_imdb_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE titles (tconst TEXT PRIMARY KEY, title_type TEXT NOT NULL, primary_title TEXT NOT NULL,
            original_title TEXT, start_year INTEGER, end_year INTEGER, runtime_min INTEGER, genres TEXT,
            avg_rating REAL, num_votes INTEGER);
        CREATE TABLE title_names (tconst TEXT NOT NULL, name TEXT NOT NULL, norm TEXT NOT NULL, kind TEXT NOT NULL,
            region TEXT, language TEXT);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    con.execute("INSERT INTO titles VALUES ('tt0086659','tvSeries','The A-Team','The A-Team',1983,1987,60,'Action',7.4,90000)")
    con.execute("INSERT INTO title_names VALUES ('tt0086659','Das A-Team','das a team','aka','DE','de')")
    con.execute("INSERT INTO title_names VALUES ('tt0086659','The A-Team','the a team','primary',NULL,NULL)")
    con.executemany("INSERT INTO meta VALUES (?,?)", [("source_date", "2026-11-05"), ("built_at", "2026-11-05T17:49:12Z")])
    con.commit()
    con.close()


@pytest.fixture
def cfg(tmp_path) -> Config:
    data = tmp_path / "data"
    data.mkdir()
    make_epg_db(data / "epg.db")
    make_imdb_db(data / "imdb.db")
    return Config(
        base_dir=tmp_path,
        epg_db=data / "epg.db",
        imdb_db=data / "imdb.db",
        app_db=data / "app.db",
        ignore_channels_file=tmp_path / "channels_ignore.txt",
        skip_categories=list(DEFAULT_SKIP_CATEGORIES),
        skip_titles=list(DEFAULT_SKIP_TITLES),
    )
