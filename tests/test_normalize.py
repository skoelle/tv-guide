"""Normalisierung: Vertrag gegen die Kopien in tools/ (SPEC 3.4)."""
import importlib.util
from pathlib import Path

from tvguide.normalize import normalize

TOOLS = Path(__file__).resolve().parent.parent / "tools"
SAMPLES = [
    "Hör' mal, wer da hämmert",
    "Das A-Team",
    "Alle unter einem Dach",
    "M*A*S*H",
    "Käse & Straße",
    "Große Liebe",
    "1984",
    "Pfarrer Braun: Adel vernichtet",
]


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_normalize_matches_tools():
    imdb = _load("imdb_build_mod", TOOLS / "imdb_build.py")
    match = _load("match_test_mod", TOOLS / "match_test.py")
    for s in SAMPLES:
        assert normalize(s) == imdb.normalize(s) == match.normalize(s)


def test_normalize_examples():
    assert normalize("Hör' mal, wer da hämmert") == "hoer mal wer da haemmert"
    assert normalize("Käse & Straße") == "kaese und strasse"
    assert normalize("M*A*S*H") == "m a s h"
