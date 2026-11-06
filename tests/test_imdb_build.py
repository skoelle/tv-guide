"""IMDb-Build mit kleinen synthetischen Rohdaten (kein Download)."""
import argparse
import csv
import gzip
import importlib.util
import sqlite3
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _load():
    spec = importlib.util.spec_from_file_location("imdb_build_mod", TOOLS / "imdb_build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, header: list[str], rows: list[list[str]]):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _raw_dir(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    _write(raw / "title.basics.tsv.gz",
           ["tconst", "titleType", "primaryTitle", "originalTitle", "isAdult",
            "startYear", "endYear", "runtimeMinutes", "genres"],
           [["tt1", "movie", "Film One", "Original One", "0", "1999", "\\N", "100", "Drama"],
            ["tt2", "tvSeries", "Series Two", "Series Two", "0", "2000", "2002", "45", "Comedy"],
            ["tt3", "movie", "Adult Film", "Adult Film", "1", "2001", "\\N", "90", "Drama"],
            ["tt4", "movie", "Low Votes", "Low Votes", "0", "2002", "\\N", "90", "Drama"],
            ["tt5", "episode", "Pilot", "Pilot", "0", "2000", "\\N", "45", "Comedy"],
            ["tt6", "episode", "Other Pilot", "Other Pilot", "0", "2001", "\\N", "45", "Comedy"]])
    _write(raw / "title.ratings.tsv.gz", ["tconst", "averageRating", "numVotes"],
           [["tt1", "8.0", "1000"], ["tt2", "7.0", "500"], ["tt3", "5.0", "10"],
            ["tt4", "6.0", "5"], ["tt5", "8.5", "50"], ["tt6", "7.0", "5"]])
    _write(raw / "title.akas.tsv.gz",
           ["titleId", "ordering", "title", "region", "language", "types", "attributes", "isOriginalTitle"],
           [["tt1", "1", "Film Eins", "DE", "de", "\\N", "\\N", "0"],
            ["tt2", "1", "Series Two", "US", "en", "\\N", "\\N", "0"]])
    _write(raw / "title.episode.tsv.gz", ["tconst", "parentTconst", "seasonNumber", "episodeNumber"],
           [["tt5", "tt2", "1", "1"], ["tt6", "tt9", "1", "1"]])
    _write(raw / "title.crew.tsv.gz", ["tconst", "directors", "writers"],
           [["tt1", "nm10", "nm11,nm12"], ["tt2", "nm13", "\\N"]])
    _write(raw / "title.principals.tsv.gz",
           ["tconst", "ordering", "nconst", "category", "job", "characters"],
           [["tt1", "1", "nm20", "actor", "\\N", '["Hero"]'],
            ["tt1", "2", "nm21", "actress", "\\N", '["Villain"]'],
            ["tt1", "20", "nm22", "actor", "\\N", '["Extra"]'],
            ["tt1", "1", "nm10", "director", "Director", "\\N"],
            ["tt2", "1", "nm23", "self", "\\N", '["Himself"]']])
    _write(raw / "name.basics.tsv.gz",
           ["nconst", "primaryName", "birthYear", "deathYear", "primaryProfession", "knownForTitles"],
           [["nm10", "Director One", "1950", "\\N", "director", "tt1"],
            ["nm11", "Writer One", "1960", "\\N", "writer", "tt1"],
            ["nm12", "Writer Two", "1970", "\\N", "writer", "tt1"],
            ["nm13", "Director Two", "1955", "\\N", "director", "tt2"],
            ["nm20", "Actor One", "1980", "\\N", "actor", "tt1"],
            ["nm21", "Actress One", "1985", "\\N", "actress", "tt1"],
            ["nm22", "Extra Actor", "1990", "\\N", "actor", "tt1"],
            ["nm23", "Host One", "1975", "\\N", "self", "tt2"]])
    return raw


def _args(tmp_path: Path, tconst_file: Path | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        db=str(tmp_path / "imdb.db"), types="movie,tvMovie,tvSeries,tvMiniSeries",
        min_votes=50, min_year=1900, adult=False, with_episodes=True,
        with_crew=True, with_cast=True, cast_limit=15,
        tconst_file=str(tconst_file) if tconst_file else None,
    )


def test_build_enriches_crew_cast_and_episodes(tmp_path):
    mod = _load()
    raw = _raw_dir(tmp_path)
    scope = tmp_path / "scope.txt"
    scope.write_text("tt1\ntt2\n", encoding="utf-8")
    mod.build(_args(tmp_path, scope), raw)

    con = sqlite3.connect(tmp_path / "imdb.db")
    con.row_factory = sqlite3.Row
    try:
        titles = {r["tconst"] for r in con.execute("SELECT tconst FROM titles")}
        assert titles == {"tt1", "tt2"}

        names = {(r["tconst"], r["name"], r["kind"]) for r in con.execute("SELECT * FROM title_names")}
        assert ("tt1", "Film Eins", "aka") in names
        assert ("tt1", "Original One", "original") in names
        assert all(not (t == "tt2" and n == "Series Two" and k == "aka") for t, n, k in names)

        episode = con.execute("SELECT * FROM episodes").fetchone()
        assert dict(episode) == {
            "tconst": "tt5", "parent_tconst": "tt2", "season": 1, "episode": 1,
            "primary_title": "Pilot", "start_year": 2000, "avg_rating": 8.5, "num_votes": 50}

        credits = {(r["tconst"], r["nconst"], r["category"])
                   for r in con.execute("SELECT * FROM credits")}
        assert ("tt1", "nm10", "director") in credits
        assert ("tt1", "nm11", "writer") in credits and ("tt1", "nm12", "writer") in credits
        assert ("tt1", "nm20", "actor") in credits and ("tt1", "nm21", "actress") in credits
        assert ("tt1", "nm22", "actor") not in credits  # ordering > cast_limit
        assert ("tt2", "nm23", "self") in credits

        people = {r["nconst"] for r in con.execute("SELECT nconst FROM people")}
        assert people == {"nm10", "nm11", "nm12", "nm13", "nm20", "nm21", "nm23"}

        meta = {r["key"]: r["value"] for r in con.execute("SELECT * FROM meta")}
        assert meta["with_crew"] == "True" and meta["with_cast"] == "True"
        assert meta["with_episodes"] == "True" and meta["scope_size"] == "2"
    finally:
        con.close()


def test_build_respects_tconst_scope(tmp_path):
    mod = _load()
    raw = _raw_dir(tmp_path)
    scope = tmp_path / "scope.txt"
    scope.write_text("tt1\n", encoding="utf-8")
    mod.build(_args(tmp_path, scope), raw)

    con = sqlite3.connect(tmp_path / "imdb.db")
    try:
        assert con.execute("SELECT count(*) FROM episodes").fetchone()[0] == 0
        assert {r[0] for r in con.execute("SELECT DISTINCT tconst FROM credits")} == {"tt1"}
        assert {r[0] for r in con.execute("SELECT nconst FROM people")} == {
            "nm10", "nm11", "nm12", "nm20", "nm21"}
    finally:
        con.close()
