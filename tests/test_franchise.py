import sqlite3
from pathlib import Path

from tvguide.config import Config
from tvguide.db import connect
from tvguide.matching import match_shows
from tvguide.normalize import normalize

EPG_SCHEMA = """
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
"""

IMDB_SCHEMA = """
CREATE TABLE titles (tconst TEXT PRIMARY KEY, title_type TEXT NOT NULL, primary_title TEXT NOT NULL,
    original_title TEXT, start_year INTEGER, end_year INTEGER, runtime_min INTEGER, genres TEXT,
    avg_rating REAL, num_votes INTEGER);
CREATE TABLE title_names (tconst TEXT NOT NULL, name TEXT NOT NULL, norm TEXT NOT NULL, kind TEXT NOT NULL,
    region TEXT, language TEXT);
CREATE TABLE episodes (tconst TEXT PRIMARY KEY, parent_tconst TEXT NOT NULL, season INTEGER,
    episode INTEGER, primary_title TEXT, start_year INTEGER, avg_rating REAL, num_votes INTEGER);
CREATE TABLE people (nconst TEXT PRIMARY KEY, name TEXT NOT NULL, birth_year INTEGER, death_year INTEGER);
CREATE TABLE credits (tconst TEXT NOT NULL, nconst TEXT NOT NULL, category TEXT NOT NULL,
    job TEXT, characters TEXT, ordering INTEGER);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

SHOWS = [
    ("Tatort - A", 90), ("Tatort - B", 90), ("Tatort - C", 90),
    ("alpha-retro: A", 30), ("alpha-retro: B", 30), ("alpha-retro: C", 30),
    ("Schneewittchen - Der Usedom-Krimi", 90),
    ("Der Usedom-Krimi - A", 90), ("Der Usedom-Krimi - B", 90), ("Der Usedom-Krimi - C", 90),
    ("Falling Like Snowflakes - Im Fokus der Liebe", 90),
    ("Einmalig - X", 45),
    ("Snooker : British Open A", 60), ("Snooker : British Open B", 60), ("Snooker : British Open C", 60),
    ("In aller Freundschaft - A", 45), ("In aller Freundschaft - B", 45),
    ("In aller Freundschaft - C", 45),
    ("In aller Freundschaft - Die jungen Ärzte: Adventskind", 45),
]

TITLES = [
    ("tt0000001", "tvSeries", "Tatort", 1970, 90, "Crime,Drama", 3512),
    ("tt0000002", "tvSeries", "Alpha Retro", 2019, 30, None, 0),
    ("tt0000003", "tvSeries", "Der Usedom-Krimi", 2013, 90, "Crime,Drama", 500),
    ("tt0000004", "tvSeries", "Schneewittchen", 1994, 25, "Adventure,Animation,Fantasy", 199),
    ("tt0000005", "tvMovie", "Falling Like Snowflakes", 2024, 90, "Romance", 995),
    ("tt0000006", "tvSeries", "Einmalig", 2020, 45, "Drama", 10),
    ("tt0000007", "movie", "Snooker", 1980, 60, "Sport", 100),
    ("tt0000008", "tvSeries", "In aller Freundschaft", 1998, 45, "Drama", 2000),
    ("tt0000009", "tvSeries", "In aller Freundschaft - Die jungen Ärzte", 2015, 45, "Drama", 800),
]


def _make_cfg(tmp_path: Path) -> Config:
    data = tmp_path / "data"
    data.mkdir()
    epg = sqlite3.connect(data / "epg.db")
    epg.executescript(EPG_SCHEMA)
    epg.execute("INSERT INTO channels VALUES ('c1','Test','1',NULL,'t')")
    base = 1793538000
    for i, (title, minutes) in enumerate(SHOWS):
        epg.execute(
            "INSERT INTO events (channel_id,start_ts,stop_ts,start_local,title,content_hash,"
            "first_seen,last_seen,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("c1", base + i * 7200, base + i * 7200 + minutes * 60, "x", title, "h", "t", "t", "t"))
    epg.execute("INSERT INTO imports (started_at, finished_at, source, events_total, events_new, events_changed)"
                " VALUES ('t','t','test',?,?,0)", (len(SHOWS), len(SHOWS)))
    epg.commit()
    epg.close()

    imdb = sqlite3.connect(data / "imdb.db")
    imdb.executescript(IMDB_SCHEMA)
    for tconst, kind, title, year, runtime, genres, votes in TITLES:
        imdb.execute("INSERT INTO titles VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (tconst, kind, title, title, year, None, runtime, genres, 7.0, votes))
        imdb.execute("INSERT INTO title_names VALUES (?,?,?,?,?,?)",
                     (tconst, title, normalize(title), "primary", None, None))
    imdb.execute("INSERT INTO meta VALUES ('source_date','2026-11-05')")
    imdb.commit()
    imdb.close()

    return Config(base_dir=tmp_path, epg_db=data / "epg.db", imdb_db=data / "imdb.db",
                  app_db=data / "app.db", ignore_channels_file=tmp_path / "channels_ignore.txt")


def _results(cfg):
    con = connect(cfg)
    try:
        results, _stats = match_shows(con, cfg)
    finally:
        con.close()
    return {s["title"]: res for s, res in results}


def test_strong_series_franchises_are_auto(tmp_path):
    res = _results(_make_cfg(tmp_path))
    for title in ("Tatort - A", "Tatort - B", "Tatort - C"):
        assert res[title]["status"] == "auto", title
        assert res[title]["best"]["tconst"] == "tt0000001"
    for title in ("alpha-retro: A", "alpha-retro: B", "alpha-retro: C"):
        assert res[title]["status"] == "auto", title
        assert res[title]["best"]["tconst"] == "tt0000002"


def test_suffix_franchise_beats_wrong_prefix(tmp_path):
    res = _results(_make_cfg(tmp_path))
    for title in ("Schneewittchen - Der Usedom-Krimi", "Der Usedom-Krimi - A",
                  "Der Usedom-Krimi - B", "Der Usedom-Krimi - C"):
        assert res[title]["best"]["tconst"] == "tt0000003", title
    assert res["Schneewittchen - Der Usedom-Krimi"]["status"] == "auto"


def test_specific_spin_off_prefix_wins(tmp_path):
    res = _results(_make_cfg(tmp_path))
    for title in ("In aller Freundschaft - A", "In aller Freundschaft - B",
                  "In aller Freundschaft - C"):
        assert res[title]["best"]["tconst"] == "tt0000008", title
    spin_off = res["In aller Freundschaft - Die jungen Ärzte: Adventskind"]
    assert spin_off["best"]["tconst"] == "tt0000009"
    assert spin_off["status"] == "auto"


def test_one_off_prefixes_stay_in_review(tmp_path):
    res = _results(_make_cfg(tmp_path))
    assert res["Falling Like Snowflakes - Im Fokus der Liebe"]["status"] == "review"
    assert res["Einmalig - X"]["status"] == "review"
    assert res["Snooker : British Open A"]["status"] != "auto"
