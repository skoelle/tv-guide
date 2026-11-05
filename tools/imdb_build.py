#!/usr/bin/env python3
"""
imdb_build.py: laedt die IMDb Non-Commercial Datasets und baut eine kleine,
gefilterte SQLite-DB (imdb.db) fuer das Matching deutscher TV-Titel.

Nur Standardbibliothek, Python 3.9+. Gedacht fuer den lokalen Rechner, die fertige
imdb.db wird danach auf die Synology kopiert. Mit dem EPG-Import hat das nichts zu tun.

Daten: https://datasets.imdbws.com/  (taeglich aktualisiert, nur persoenliche und
nicht kommerzielle Nutzung, siehe https://developer.imdb.com/non-commercial-datasets/ ).
Bitte die IMDb-Bedingungen einhalten (u.a. Quellenangabe, keine Weitergabe).

Beispiele:
  ./imdb_build.py                              # Download nach ./imdb_raw, Build nach ./data/imdb.db
  ./imdb_build.py --no-download                # vorhandene Rohdateien in ./imdb_raw verwenden
  ./imdb_build.py --min-votes 50 --min-year 1950
  ./imdb_build.py --with-episodes              # zusaetzlich Episoden (deutlich groesser)
"""
import argparse
import csv
import gzip
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE = "https://datasets.imdbws.com/"
FILES = ["title.basics.tsv.gz", "title.akas.tsv.gz", "title.ratings.tsv.gz"]
EPISODE_FILE = "title.episode.tsv.gz"
DEFAULT_TYPES = "movie,tvMovie,tvSeries,tvMiniSeries"
AKA_REGIONS = {"DE", "AT", "CH"}

csv.field_size_limit(1 << 24)

_PUNCT = re.compile(r"[^a-z0-9]+")


def normalize(title: str) -> str:
    """Normalisierung fuer den Titelvergleich. Dieselbe Funktion muss spaeter beim
    Matching der EPG-Titel verwendet werden."""
    t = title.lower().replace("\u00df", "ss")
    t = t.replace("\u00e4", "ae").replace("\u00f6", "oe").replace("\u00fc", "ue")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("&", " und ")
    return _PUNCT.sub(" ", t).strip()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def remote_mtime(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "imdb-build/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        lm = r.headers.get("Last-Modified")
    return parsedate_to_datetime(lm).timestamp() if lm else None


def download(name, raw_dir, force):
    url = BASE + name
    dest = raw_dir / name
    try:
        rm = remote_mtime(url)
    except Exception as e:
        log(f"HEAD fehlgeschlagen fuer {name}: {e}")
        rm = None
    if dest.exists() and not force and rm and dest.stat().st_mtime >= rm:
        log(f"{name}: aktuell, ueberspringe Download")
        return dest
    log(f"Lade {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "imdb-build/1.0"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total and done % (50 << 20) < (1 << 20):
                log(f"  {name}: {done / 1e6:.0f}/{total / 1e6:.0f} MB")
    os.replace(tmp, dest)
    if rm:
        os.utime(dest, (rm, rm))
    log(f"{name}: {dest.stat().st_size / 1e6:.0f} MB in {time.time() - t0:.0f}s")
    return dest


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader)
        for r in reader:
            yield header, r


def nz(v):
    return None if v == "\\N" else v


def to_int(v):
    v = nz(v)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


SCHEMA = """
CREATE TABLE titles (
    tconst        TEXT PRIMARY KEY,
    title_type    TEXT NOT NULL,
    primary_title TEXT NOT NULL,
    original_title TEXT,
    start_year    INTEGER,
    end_year      INTEGER,
    runtime_min   INTEGER,
    genres        TEXT,
    avg_rating    REAL,
    num_votes     INTEGER
);
CREATE TABLE title_names (
    tconst   TEXT NOT NULL,
    name     TEXT NOT NULL,
    norm     TEXT NOT NULL,
    kind     TEXT NOT NULL,          -- primary | original | aka
    region   TEXT,
    language TEXT
);
CREATE TABLE episodes (
    tconst        TEXT PRIMARY KEY,
    parent_tconst TEXT NOT NULL,
    season        INTEGER,
    episode       INTEGER
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

INDEXES = """
CREATE INDEX idx_names_norm   ON title_names(norm);
CREATE INDEX idx_names_tconst ON title_names(tconst);
CREATE INDEX idx_titles_year  ON titles(start_year);
CREATE INDEX idx_titles_type  ON titles(title_type);
CREATE INDEX idx_titles_votes ON titles(num_votes);
CREATE INDEX idx_episodes_parent ON episodes(parent_tconst);
"""


def build(args, raw_dir):
    types = {t.strip() for t in args.types.split(",") if t.strip()}
    out = Path(args.db)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.executescript(SCHEMA)

    log("Lese ratings")
    ratings = {}
    for _, r in rows(raw_dir / "title.ratings.tsv.gz"):
        try:
            ratings[r[0]] = (float(r[1]), int(r[2]))
        except (ValueError, IndexError):
            pass

    log("Filtere title.basics")
    kept = {}
    batch, names = [], []
    n_in = 0
    for h, r in rows(raw_dir / "title.basics.tsv.gz"):
        n_in += 1
        if r[1] not in types:
            continue
        if not args.adult and r[4] == "1":
            continue
        year = to_int(r[5])
        if args.min_year and (year is None or year < args.min_year):
            continue
        rating, votes = ratings.get(r[0], (None, 0))
        if votes < args.min_votes:
            continue
        kept[r[0]] = True
        batch.append((r[0], r[1], r[2], nz(r[3]), year, to_int(r[6]), to_int(r[7]),
                      nz(r[8]), rating, votes))
        names.append((r[0], r[2], normalize(r[2]), "primary", None, None))
        if nz(r[3]) and r[3] != r[2]:
            names.append((r[0], r[3], normalize(r[3]), "original", None, None))
    con.executemany("INSERT INTO titles VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
    con.executemany("INSERT INTO title_names VALUES (?,?,?,?,?,?)", names)
    log(f"basics: {n_in:,} gelesen, {len(batch):,} behalten")
    del ratings, batch, names

    log("Filtere title.akas (DE/AT/CH und Originaltitel), das dauert einige Minuten")
    seen = set()
    buf = []
    n_in = n_out = 0
    for h, r in rows(raw_dir / "title.akas.tsv.gz"):
        n_in += 1
        tid = r[0]
        if tid not in kept:
            continue
        region, lang, title = nz(r[3]), nz(r[4]), r[2]
        is_orig = r[7] == "1"
        if region not in AKA_REGIONS and lang != "de" and not is_orig:
            continue
        norm = normalize(title)
        if not norm or (tid, norm) in seen:
            continue
        seen.add((tid, norm))
        buf.append((tid, title, norm, "aka", region, lang))
        n_out += 1
        if len(buf) >= 100000:
            con.executemany("INSERT INTO title_names VALUES (?,?,?,?,?,?)", buf)
            buf.clear()
    con.executemany("INSERT INTO title_names VALUES (?,?,?,?,?,?)", buf)
    log(f"akas: {n_in:,} gelesen, {n_out:,} behalten")

    if args.with_episodes:
        log("Lese title.episode")
        eb = []
        for _, r in rows(raw_dir / EPISODE_FILE):
            if r[1] in kept:
                eb.append((r[0], r[1], to_int(r[2]), to_int(r[3])))
                if len(eb) >= 100000:
                    con.executemany("INSERT INTO episodes VALUES (?,?,?,?)", eb)
                    eb.clear()
        con.executemany("INSERT INTO episodes VALUES (?,?,?,?)", eb)

    log("Baue Indizes")
    con.executescript(INDEXES)
    src_ts = max((raw_dir / f).stat().st_mtime for f in FILES)
    meta = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_date": datetime.fromtimestamp(src_ts, timezone.utc).strftime("%Y-%m-%d"),
        "types": ",".join(sorted(types)),
        "min_votes": str(args.min_votes),
        "min_year": str(args.min_year or ""),
        "with_episodes": str(bool(args.with_episodes)),
        "attribution": "Information courtesy of IMDb (https://www.imdb.com). Used with permission for personal, non-commercial use.",
    }
    con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    con.commit()
    stats = {
        "titles": con.execute("SELECT COUNT(*) FROM titles").fetchone()[0],
        "names": con.execute("SELECT COUNT(*) FROM title_names").fetchone()[0],
    }
    con.execute("VACUUM")
    con.close()
    os.replace(tmp, out)
    log(f"Fertig: {stats['titles']:,} Titel, {stats['names']:,} Namen, {out.stat().st_size / 1e6:.0f} MB -> {out}")


def main():
    ap = argparse.ArgumentParser(description="IMDb Datasets -> gefilterte SQLite")
    ap.add_argument("--raw-dir", default="imdb_raw", help="Ordner fuer die heruntergeladenen .tsv.gz")
    ap.add_argument("--db", default="data/imdb.db", help="Ausgabe-DB")
    ap.add_argument("--no-download", action="store_true", help="Vorhandene Rohdateien verwenden")
    ap.add_argument("--force", action="store_true", help="Download erzwingen")
    ap.add_argument("--types", default=DEFAULT_TYPES, help=f"titleType-Filter (Default: {DEFAULT_TYPES})")
    ap.add_argument("--min-votes", type=int, default=0, help="Mindestanzahl Stimmen (Default 0)")
    ap.add_argument("--min-year", type=int, default=None, help="fruehestes startYear")
    ap.add_argument("--adult", action="store_true", help="Adult-Titel einschliessen")
    ap.add_argument("--with-episodes", action="store_true", help="title.episode mit importieren")
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    files = FILES + ([EPISODE_FILE] if args.with_episodes else [])
    if not args.no_download:
        for f in files:
            download(f, raw_dir, args.force)
    missing = [f for f in files if not (raw_dir / f).exists()]
    if missing:
        log(f"Fehlende Dateien in {raw_dir}: {', '.join(missing)}")
        return 1
    build(args, raw_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
