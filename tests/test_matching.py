import sqlite3
from collections import Counter

import pytest

from tvguide.db import connect
from tvguide.matching import match_show, retro_class, run_match, score, skip_reason, suggestions


def _show(title, durs, cats=None, subs=None, n=None, desc=""):
    return {
        "title": title,
        "n": n if n is not None else len(durs),
        "durs": list(durs),
        "cats": Counter(cats or {}),
        "subs": set(subs or []),
        "desc": desc,
        "yhint": None,
        "chs": Counter(),
    }


def test_score_signals_sum_to_total():
    row = {"title_type": "tvSeries", "runtime_min": 60, "num_votes": 1000, "start_year": 1985}
    total, signals = score("macgyver", "full", "macgyver", row, 60, 5, 3, "")
    assert signals["name"] == 1.0
    assert signals["series_duration"] == 0.15
    assert signals["series_seriesish"] == 0.1
    assert sum(signals.values()) == pytest.approx(total)


def test_skip_rules(cfg):
    assert skip_reason(_show("Teleshopping", [60]), cfg) == "skip_title"
    assert skip_reason(_show("Kurzes", [5, 5]), cfg) == "skip_short"
    assert skip_reason(_show("Strecke", [120] * 12), cfg) == "skip_block"
    assert skip_reason(_show("Brisant", [20], cats={'["News / Current affairs"]': 5}), cfg) == "skip_category"
    assert skip_reason(_show("MacGyver", [60], cats={'["Movie / Drama"]': 1}), cfg) is None


def test_match_show_uses_aka(cfg):
    con = connect(cfg)
    s = _show("Das A-Team", [60, 60, 60, 60], subs={"a", "b"})
    res = match_show(con, s, cfg)
    con.close()
    assert res["best"]["tconst"] == "tt0086659"
    assert res["status"] == "auto"
    assert res["reason"]["signals"]["name"] == 1.0


def test_match_show_reconstructs_title_from_aliases(cfg):
    con = sqlite3.connect(cfg.imdb_db)
    con.execute(
        "INSERT INTO titles VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("tt13273744", "tvMovie", "A Christmas Carousel", "A Christmas Carousel",
         2020, None, 85, "Drama,Romance", 6.1, 1466))
    con.executemany(
        "INSERT INTO title_names VALUES (?,?,?,?,?,?)",
        [("tt13273744", "A Christmas Carousel", "a christmas carousel", "primary", None, None),
         ("tt13273744", "Das Weihnachtskarussell", "das weihnachtskarussell", "aka", "DE", "de"),
         ("tt13273744", "Alles dreht sich um die Liebe", "alles dreht sich um die liebe",
          "aka", "DE", "de")])
    con.commit()
    con.close()

    con = connect(cfg)
    s = _show("Das Weihnachtskarussell - Alles dreht sich um die Liebe", [102, 105])
    res = match_show(con, s, cfg)
    con.close()
    assert res["best"]["tconst"] == "tt13273744"
    assert res["status"] == "auto"
    assert res["reason"]["signals"]["combined_name"] == cfg.combined_name_bonus


def test_match_show_uses_original_title_prefix_with_subtitle(cfg):
    con = sqlite3.connect(cfg.imdb_db)
    con.execute(
        "INSERT INTO titles VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("tt32580848", "tvMovie", "A Vintage Christmas", "A Christmas Post",
         2024, None, 89, "Romance", 6.0, 618))
    con.executemany(
        "INSERT INTO title_names VALUES (?,?,?,?,?,?)",
        [("tt32580848", "A Vintage Christmas", "a vintage christmas", "primary", None, None),
         ("tt32580848", "A Christmas Post", "a christmas post", "original", None, None)])
    con.commit()
    con.close()

    con = connect(cfg)
    s = _show("A Christmas Post - Die vergessenen Weihnachtswünsche", [105])
    res = match_show(con, s, cfg)
    con.close()
    assert res["best"]["tconst"] == "tt32580848"
    assert res["status"] == "auto"
    assert res["reason"]["signals"]["original_prefix"] == cfg.original_prefix_bonus


def test_match_show_single_exact_film_ignores_low_votes(cfg):
    con = sqlite3.connect(cfg.imdb_db)
    con.execute(
        "INSERT INTO titles VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("tt0157287", "tvMovie", "Abgehauen", "Abgehauen",
         1998, None, 90, "Biography,Drama", 6.4, 49))
    con.executemany(
        "INSERT INTO title_names VALUES (?,?,?,?,?,?)",
        [("tt0157287", "Abgehauen", "abgehauen", "primary", None, None),
         ("tt0157287", "Abgehauen", "abgehauen", "aka", None, None)])
    con.commit()
    con.close()

    con = connect(cfg)
    s = _show("Abgehauen", [90])
    res = match_show(con, s, cfg)
    con.close()
    assert res["best"]["tconst"] == "tt0157287"
    assert res["status"] == "auto"
    assert res["reason"]["signals"]["single_exact"] == cfg.single_exact_bonus


def test_match_show_single_exact_needs_unique_candidate(cfg):
    con = sqlite3.connect(cfg.imdb_db)
    con.execute(
        "INSERT INTO titles VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("tt0157287", "tvMovie", "Abgehauen", "Abgehauen",
         1998, None, 90, "Biography,Drama", 6.4, 49))
    con.execute(
        "INSERT INTO titles VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("tt0999999", "tvMovie", "Abgehauen", "Abgehauen",
         1998, None, 90, "Drama", 6.0, 10))
    con.executemany(
        "INSERT INTO title_names VALUES (?,?,?,?,?,?)",
        [("tt0157287", "Abgehauen", "abgehauen", "primary", None, None),
         ("tt0999999", "Abgehauen", "abgehauen", "primary", None, None)])
    con.commit()
    con.close()

    con = connect(cfg)
    s = _show("Abgehauen", [90])
    res = match_show(con, s, cfg)
    con.close()
    assert res["status"] == "review"
    assert "single_exact" not in res["reason"]["signals"]


def _insert_title(con, tconst, title_type, primary, original, year, runtime, genres, votes, names):
    con.execute(
        "INSERT INTO titles VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tconst, title_type, primary, original, year, None, runtime, genres, 6.0, votes))
    con.executemany(
        "INSERT INTO title_names VALUES (?,?,?,?,?,?)",
        [(tconst, name, norm, kind, None, None) for name, norm, kind in names])
    con.commit()


def test_match_show_exact_unique_short_documentary_ignores_penalties(cfg):
    con = sqlite3.connect(cfg.imdb_db)
    _insert_title(con, "tt9000001", "tvMovie", "Allianz gegen China", "Allianz gegen China",
                  2025, 55, "Documentary", 12,
                  [("Allianz gegen China", "allianz gegen china", "primary")])
    con.close()

    con = connect(cfg)
    s = _show("Allianz gegen China", [55], subs={"Deutschland 2025"})
    res = match_show(con, s, cfg)
    con.close()
    assert res["best"]["tconst"] == "tt9000001"
    assert res["status"] == "auto"
    assert res["reason"]["signals"]["exact_unique"] > 0


def test_score_uses_subtitle_year_once(cfg):
    row = {"title_type": "movie", "runtime_min": 90, "num_votes": 1000, "start_year": 2020,
           "primary_title": "X", "original_title": "X"}
    _total, signals = score("x", "full", "x", row, 90, 1, 0, "USA 2020", {2020})
    assert signals["year_in_desc"] == 0.1
    assert "year_in_subtitle" not in signals

    _total, signals = score("x", "full", "x", row, 90, 1, 0, "", {2020})
    assert signals["year_in_subtitle"] == 0.1
    assert "year_in_desc" not in signals

    series = dict(row, title_type="tvSeries")
    _total, signals = score("x", "full", "x", series, 90, 1, 0, "", {2020})
    assert "year_in_subtitle" not in signals


def test_match_show_prefers_subtitle_year(cfg):
    con = sqlite3.connect(cfg.imdb_db)
    _insert_title(con, "tt9000101", "movie", "Schneewittchen", "Schneewittchen",
                  1916, 60, "Fantasy", 500,
                  [("Schneewittchen", "schneewittchen", "primary")])
    _insert_title(con, "tt9000102", "movie", "Schneewittchen", "Schneewittchen",
                  1961, 60, "Fantasy", 500,
                  [("Schneewittchen", "schneewittchen", "primary")])
    con.close()

    con = connect(cfg)
    s = _show("Schneewittchen", [60], subs={"Spielfilm /DDR 1961"})
    res = match_show(con, s, cfg)
    su = suggestions(con, s, cfg, 5)
    con.close()
    assert res["best"]["tconst"] == "tt9000102"
    assert res["status"] == "auto"
    assert res["reason"]["signals"]["year_in_subtitle"] == 0.1
    assert [x["score"] for x in su] == sorted((x["score"] for x in su), reverse=True)


def test_match_show_exact_unique_needs_runtime_fit(cfg):
    con = sqlite3.connect(cfg.imdb_db)
    _insert_title(con, "tt9000002", "tvMovie", "Kurzer Film", "Kurzer Film",
                  2025, 90, "Drama", 1000,
                  [("Kurzer Film", "kurzer film", "primary")])
    con.close()

    con = connect(cfg)
    s = _show("Kurzer Film", [45])
    res = match_show(con, s, cfg)
    con.close()
    assert res["status"] == "review"
    assert "exact_unique" not in res["reason"]["signals"]


def test_match_show_exact_unique_rejects_subtitle_year_mismatch(cfg):
    con = sqlite3.connect(cfg.imdb_db)
    _insert_title(con, "tt9000003", "tvMovie", "Alter Film", "Alter Film",
                  1987, 55, "Drama", 0,
                  [("Alter Film", "alter film", "primary")])
    con.close()

    con = connect(cfg)
    s = _show("Alter Film", [55], subs={"Deutschland 2024"})
    res = match_show(con, s, cfg)
    con.close()
    assert res["status"] == "review"
    assert "exact_unique" not in res["reason"]["signals"]


def test_match_show_exact_unique_alias_without_primary(cfg):
    con = sqlite3.connect(cfg.imdb_db)
    _insert_title(con, "tt9000004", "movie", "Crocodiles Revealed", None,
                  2022, None, "Documentary", 0,
                  [("Okavango: Auf Tauchgang mit den Krokodilen",
                    "okavango auf tauchgang mit den krokodilen", "aka")])
    con.close()

    con = connect(cfg)
    s = _show("Okavango: Auf Tauchgang mit den Krokodilen", [45], subs={"USA 2022"})
    res = match_show(con, s, cfg)
    con.close()
    assert res["status"] == "auto"
    assert "single_exact" not in res["reason"]["signals"]
    assert res["reason"]["signals"]["exact_unique"] > 0


def test_retro_class(cfg):
    class R(dict):
        def __getitem__(self, k):
            return self.get(k)

    film = R(title_type="movie", start_year=1985, end_year=None, genres="Action")
    serie = R(title_type="tvSeries", start_year=1990, end_year=1995, genres="Comedy")
    offen = R(title_type="tvSeries", start_year=1990, end_year=None, genres="Comedy")
    lang = R(title_type="tvSeries", start_year=1990, end_year=2015, genres="Comedy")
    news = R(title_type="movie", start_year=1985, end_year=None, genres="News")
    assert retro_class(film, cfg) == "film"
    assert retro_class(serie, cfg) == "serie_abgeschlossen"
    assert retro_class(offen, cfg) == "serie_laufend"
    assert retro_class(lang, cfg) == "serie_lief_laenger"
    assert retro_class(news, cfg) == ""


def test_run_match_persists_and_is_idempotent(cfg):
    con = connect(cfg)
    run_match(con, cfg)
    shows = con.execute("SELECT count(*) FROM epg_shows").fetchone()[0]
    matches = con.execute("SELECT count(*) FROM show_match").fetchone()[0]
    events = con.execute("SELECT count(*) FROM event_show").fetchone()[0]
    assert shows == 3 and matches == 3 and events == 3
    assert {r[0] for r in con.execute("SELECT status FROM show_match")} == {"none"}
    run_match(con, cfg)
    assert con.execute("SELECT count(*) FROM show_match").fetchone()[0] == matches
    con.close()


def test_run_match_entfernt_ignorierte_shows(cfg):
    con = connect(cfg)
    run_match(con, cfg)
    assert con.execute("SELECT count(*) FROM epg_shows").fetchone()[0] == 3
    cfg.ignore_channels_file.write_text("Kabel Eins\n", encoding="utf-8")
    run_match(con, cfg)
    assert con.execute("SELECT count(*) FROM epg_shows").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM show_match").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM event_show").fetchone()[0] == 0
    con.close()


def test_override_has_precedence(cfg):
    con = connect(cfg)
    con.execute("INSERT INTO overrides (show_key, tconst, note, created_at) VALUES (?,?,?,?)",
                ("sendung 0", "tt0086659", "test", "2026-11-05T00:00:00Z"))
    run_match(con, cfg)
    row = con.execute(
        """SELECT sm.status, sm.tconst, sm.method FROM show_match sm
           JOIN epg_shows s ON s.id = sm.show_id WHERE s.show_key = 'sendung 0'""").fetchone()
    con.close()
    assert tuple(row) == ("confirmed", "tt0086659", "override")
