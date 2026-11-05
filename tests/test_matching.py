from collections import Counter

import pytest

from tvguide.db import connect
from tvguide.matching import match_show, retro_class, run_match, score, skip_reason


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
