"""Titelbereinigung und Kandidatenbildung (aus tools/match_test.py v2.1)."""
from __future__ import annotations

import re

from .normalize import normalize

# Folgenmarker, Qualitaets-Tags und Wiederholungshinweise am Titelende.
CLEAN_PATTERNS = [
    re.compile(r"\s*\((?:hd|wh|wdh|ut|ad|s/w|omu|ov)\)\s*$", re.I),
    re.compile(r"\s*\(S\d+\s*/?\s*E\d+\)\s*$", re.I),
    re.compile(r"\s*\(?\s*(?:teil|folge)\s*\d+\s*(?:von|/)\s*\d+\s*\)?\s*$", re.I),
    re.compile(r"\s*\(\d+\s*/\s*\d+\)\s*$"),
    re.compile(r"\s*[-:,]?\s*(?:teil|folge|staffel|episode|ep\.?)\s*\d+(?:\s*/\s*\d+)?\s*$", re.I),
    re.compile(r"\s+\(\d{1,4}\)\s*$"),
    re.compile(r"\s+wiederholung\s*$", re.I),
]
YEAR_IN_TITLE = re.compile(r"\s*\(((?:19[2-9]\d|20[0-3]\d))\)\s*$")
YEAR_IN_DESC = re.compile(r"\b(19[2-9]\d|20[0-2]\d)\b")


def clean_title(t: str) -> tuple[str, int | None, bool]:
    """Gibt (bereinigter_titel, jahr_hinweis, hatte_marker) zurueck."""
    year, changed = None, False
    m = YEAR_IN_TITLE.search(t)
    if m:
        year = int(m.group(1))
        t = t[:m.start()]
    for _ in range(4):
        before = t
        for p in CLEAN_PATTERNS:
            t = p.sub("", t)
        if t == before:
            break
        changed = True
    return t.strip(), year, changed


def candidates(title: str) -> list[tuple[str, str, str]]:
    """Liefert (text, norm, kind) fuer vollen Titel, Praefixe und Suffix."""
    out = [(title, "full")]
    parts = [p for p in re.split(r"\s+[-\u2013\u2014]\s+|:\s+|\s+:\s+", title) if p.strip()]
    if len(parts) > 1:
        for k in range(len(parts) - 1, 0, -1):
            out.append((" ".join(parts[:k]) if k > 1 else parts[0], f"prefix{k}"))
        out.append((parts[-1], "suffix"))
    seen, res = set(), []
    for text, kind in out:
        n = normalize(text)
        if n and n not in seen:
            seen.add(n)
            res.append((text, n, kind))
    return res
