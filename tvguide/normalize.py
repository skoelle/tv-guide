"""Normalisierung von Titeln. Vertrag zwischen IMDb-Namen und EPG-Titeln.

Muss identisch zu den Kopien in `tools/imdb_build.py` und `tools/match_test.py` sein
(siehe SPEC.md 3.4). Aenderungen erfordern einen Neuaufbau von `imdb.db`.
"""
from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^a-z0-9]+")


def normalize(title: str) -> str:
    """Kleinschreibung, ß->ss, ä/ö/ü->ae/oe/ue, Diakritika weg, & -> und, Rest -> Leerzeichen."""
    t = title.lower().replace("\u00df", "ss")
    t = t.replace("\u00e4", "ae").replace("\u00f6", "oe").replace("\u00fc", "ue")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("&", " und ")
    return _PUNCT.sub(" ", t).strip()
