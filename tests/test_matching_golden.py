"""Golden-Test gegen einen echten match_test.py-Lauf (SPEC/PLAN M2).

Benoetigt die echten data/*.db und channels_ignore.txt; fehlen sie, wird der Test
uebersprungen, damit CI ohne die grossen Datenbanken gruen bleibt.
"""
import csv
from pathlib import Path

import pytest

from tvguide.config import Config, load_config
from tvguide.db import connect
from tvguide.matching import match_shows

REPO = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "matches.csv"

HAS_DATA = (REPO / "data" / "epg.db").exists() and (REPO / "data" / "imdb.db").exists()
HAS_IGNORE = (REPO / "channels_ignore.txt").exists()

pytestmark = pytest.mark.skipif(
    not (HAS_DATA and HAS_IGNORE),
    reason="Echtdaten oder channels_ignore.txt fehlen",
)


def _config() -> Config:
    cfg_file = REPO / "config.toml"
    if cfg_file.exists():
        return load_config(cfg_file)
    return Config(
        base_dir=REPO,
        epg_db=REPO / "data" / "epg.db",
        imdb_db=REPO / "data" / "imdb.db",
        app_db=REPO / "data" / "app.db",
        ignore_channels_file=REPO / "channels_ignore.txt",
    )


def test_golden_gegen_referenzlauf():
    cfg = _config()
    con = connect(cfg)
    try:
        results, _stats = match_shows(con, cfg)
    finally:
        con.close()

    got = {s["title"]: res for s, res in results}
    fehler = []
    for r in csv.DictReader(open(GOLDEN, encoding="utf-8")):
        res = got.get(r["title"])
        if res is None:
            fehler.append(f"{r['title']}: Show nicht gefunden")
            continue
        if res["status"] != r["status"]:
            fehler.append(f"{r['title']}: status {res['status']} != {r['status']}")
            continue
        tconst = res["best"]["tconst"] if res["best"] else ""
        if (tconst or "") != r["tconst"]:
            fehler.append(f"{r['title']}: tconst {tconst!r} != {r['tconst']!r}")
    assert not fehler, "Abweichungen:\n" + "\n".join(fehler[:25])
