"""Inhaltskategorien: Film, Serie oder Format (Doku/Magazin/Show).

Die Kategorie wird aus IMDb-Typ und -Genres abgeleitet. Manuelle Markierungen
(`content_marks`, siehe `tvguide/marks.py`) koennen sie ueberschreiben:
"format" -> immer Format, "keep" -> als Film/Serie behalten.
"""
from __future__ import annotations

MOVIE_TYPES = {"movie", "tvMovie"}

# Genres, die auf Doku/Magazin/Show/Nachrichten hindeuten (keine Fiktion-Serie).
FORMAT_GENRES = {
    "News", "Talk-Show", "Game-Show", "Reality-TV", "Documentary",
    "Music", "Sport", "Short",
}

CATEGORIES = ("film", "serie", "format")


def derived_category(title_type: str | None, genres: str | None) -> str:
    if title_type in MOVIE_TYPES:
        return "film"
    g = set((genres or "").split(","))
    if g & FORMAT_GENRES:
        return "format"
    return "serie"


def category(title_type: str | None, genres: str | None, mark: str | None = None) -> str:
    if mark == "format":
        return "format"
    if mark == "keep":
        return "film" if title_type in MOVIE_TYPES else "serie"
    return derived_category(title_type, genres)


def label(cat: str | None) -> str:
    return {"film": "Film", "serie": "Serie", "format": "Format"}.get(cat or "", "Serie")
