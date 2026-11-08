import sqlite3

from tvguide.db import MIGRATIONS, connect
from tvguide.status import collect, fmt_iso, fmt_num, fmt_size, fmt_ts, render_text


def test_collect_numbers(cfg):
    con = connect(cfg)
    s = collect(con, cfg)
    con.close()
    assert s["epg"]["channels"] == 2
    assert s["epg"]["events"] == 3
    assert s["epg"]["last_import"]["events_new"] == 3
    assert s["epg"]["last_changed_import"]["events_new"] == 3
    assert s["imdb"]["titles"] == 1
    assert s["imdb"]["names"] == 2
    assert s["imdb"]["meta"]["source_date"] == "2026-11-05"
    assert s["app"]["schema_version"] == len(MIGRATIONS)
    assert s["app"]["shows"] == 0
    assert s["app"]["last_assignment"] is None
    assert s["support"] == {"enabled": False, "sql_enabled": False}
    assert s["problems"] == []


def test_last_changed_import_ignores_unchanged_runs(cfg):
    raw = sqlite3.connect(cfg.epg_db)
    raw.execute(
        "INSERT INTO imports (started_at, finished_at, source, events_total, events_new, events_changed, events_pruned)"
        " VALUES ('2026-11-06T17:50:00Z','2026-11-06T17:50:30Z','test',3,0,0,0)")
    raw.commit()
    raw.close()
    con = connect(cfg)
    s = collect(con, cfg)
    con.close()
    assert s["epg"]["last_import"]["finished_at"] == "2026-11-06T17:50:30Z"
    assert s["epg"]["last_import"]["events_new"] == 0
    assert s["epg"]["last_changed_import"]["finished_at"] == "2026-11-05T17:50:30Z"
    assert s["epg"]["last_changed_import"]["events_new"] == 3


def test_problems_for_missing_files(cfg):
    cfg.imdb_db.unlink()
    con = connect(cfg)
    s = collect(con, cfg)
    con.close()
    assert any("imdb.db fehlt" in p for p in s["problems"])


def test_text_output(cfg):
    con = connect(cfg)
    text = render_text(collect(con, cfg))
    con.close()
    assert "Sendungen:  3" in text
    assert "Titel:      1" in text
    assert "noch keine" in text
    assert "TMDB-Poster" in text
    assert "Schema-Version" in text


def test_formatters():
    assert fmt_num(1285899) == "1.285.899"
    assert fmt_num(None) == "-"
    assert fmt_size(493375488) == "470,5 MB"
    assert fmt_ts(1793538000, "Europe/Berlin") == "01.11.2026 14:00"
    assert fmt_iso("2026-11-05T17:50:30Z", "Europe/Berlin") == "05.11.2026 18:50"
    assert fmt_iso(None, "Europe/Berlin") == "-"
    assert fmt_iso("kaputt", "Europe/Berlin") == "kaputt"


def test_collect_support_status(cfg):
    from dataclasses import replace

    con = connect(cfg)
    s = collect(con, replace(cfg, support_token="secret", support_sql_enabled=True))
    con.close()
    assert s["support"] == {"enabled": True, "sql_enabled": True}


def test_collect_last_assignment(cfg):
    from tvguide.matching import run_match

    con = connect(cfg)
    run_match(con, cfg)
    s = collect(con, cfg)
    con.close()
    assert s["app"]["last_assignment"] is not None
