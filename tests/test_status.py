from tvguide.db import MIGRATIONS, connect
from tvguide.status import collect, fmt_num, fmt_size, fmt_ts, render_text


def test_collect_numbers(cfg):
    con = connect(cfg)
    s = collect(con, cfg)
    con.close()
    assert s["epg"]["channels"] == 2
    assert s["epg"]["events"] == 3
    assert s["epg"]["last_import"]["events_new"] == 3
    assert s["imdb"]["titles"] == 1
    assert s["imdb"]["names"] == 2
    assert s["imdb"]["meta"]["source_date"] == "2026-11-05"
    assert s["app"]["schema_version"] == len(MIGRATIONS)
    assert s["app"]["shows"] == 0
    assert s["problems"] == []


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


def test_formatters():
    assert fmt_num(1285899) == "1.285.899"
    assert fmt_num(None) == "-"
    assert fmt_size(493375488) == "470,5 MB"
    assert fmt_ts(1793538000, "Europe/Berlin") == "01.11.2026 14:00"
