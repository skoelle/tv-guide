"""Poster von TMDB laden und lokal cachen.

Die vollstaendigen TMDB-Antworten landen in `tmdb_cache` (app.db), die Bilder
unter `data/posters/`. So koennen spaetere Funktionen die Rohdaten nutzen, ohne
erneut bei TMDB anzufragen. TMDB erlaubt Caching fuer maximal sechs Monate.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .config import Config

TMDB_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
TCONST_RE = re.compile(r"^tt\d{5,}$")

_MOVIE_TYPES = ("movie", "tvMovie")


class PosterError(RuntimeError):
    """Fehler beim Poster-Abruf (Konfiguration, Netz, ungueltige Antwort)."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_days(value: str | None) -> float:
    if not value:
        return 1e9
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 1e9
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _auth() -> tuple[dict[str, str], dict[str, str]]:
    """Bevorzugt den v4 Read Access Token, sonst den v3 API Key."""
    token = os.environ.get("TVGUIDE_TMDB_READ_TOKEN", "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}, {}
    key = os.environ.get("TVGUIDE_TMDB_API_KEY", "").strip()
    if key:
        return {}, {"api_key": key}
    raise PosterError("TVGUIDE_TMDB_READ_TOKEN oder TVGUIDE_TMDB_API_KEY ist nicht gesetzt")


class TmdbClient:
    def __init__(self, cfg: Config, client: httpx.Client | None = None):
        headers, params = ({}, {}) if client is not None else _auth()
        self.cfg = cfg
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=TMDB_BASE,
            headers=headers,
            params=params,
            timeout=20.0,
            follow_redirects=True,
        )
        self._image_base = IMAGE_BASE

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        """GET mit einfacher 429-Behandlung."""
        for attempt in range(2):
            response = self._client.get(url, params=params)
            if response.status_code != 429 or attempt == 1:
                return response
            try:
                wait = int(response.headers.get("retry-after", "5"))
            except ValueError:
                wait = 5
            time.sleep(min(max(wait, 1), 60))
        raise AssertionError("unreachable")

    def configuration(self) -> dict:
        response = self._get("/configuration")
        response.raise_for_status()
        payload = response.json()
        base = (payload.get("images") or {}).get("secure_base_url")
        if base:
            self._image_base = str(base)
        if self.cfg.poster_request_delay > 0:
            time.sleep(self.cfg.poster_request_delay)
        return payload

    def find(self, tconst: str) -> dict:
        response = self._get(f"/find/{tconst}", {
            "external_source": "imdb_id",
            "language": self.cfg.poster_language,
        })
        response.raise_for_status()
        if self.cfg.poster_request_delay > 0:
            time.sleep(self.cfg.poster_request_delay)
        return response.json()

    def details(self, media_type: str, tmdb_id: int, language: str) -> dict:
        response = self._get(f"/{media_type}/{tmdb_id}", {"language": language})
        response.raise_for_status()
        if self.cfg.poster_request_delay > 0:
            time.sleep(self.cfg.poster_request_delay)
        return response.json()

    def image_url(self, poster_path: str) -> str:
        return f"{self._image_base}{self.cfg.poster_image_size}{poster_path}"

    def download(self, url: str) -> bytes:
        response = self._get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise PosterError(f"unerwarteter Content-Type: {content_type or 'unbekannt'}")
        if len(response.content) > MAX_IMAGE_BYTES:
            raise PosterError("Bild ist zu gross")
        return response.content


def _cache_payload(con: sqlite3.Connection, key: str, payload: dict) -> None:
    con.execute(
        "INSERT INTO tmdb_cache (cache_key, payload, fetched_at) VALUES (?,?,?) "
        "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at",
        (key, json.dumps(payload, ensure_ascii=False), _now()),
    )


def _choose(payload: dict, title_type: str | None) -> dict | None:
    movie_results = payload.get("movie_results") or []
    tv_results = payload.get("tv_results") or []
    if title_type in _MOVIE_TYPES:
        results, media_type = movie_results, "movie"
        if not results:
            results, media_type = tv_results, "tv"
    else:
        results, media_type = tv_results, "tv"
        if not results:
            results, media_type = movie_results, "movie"
    if not results:
        return None
    result = results[0]
    return {
        "tmdb_id": result.get("id"),
        "media_type": media_type,
        "poster_path": result.get("poster_path"),
    }


def _poster_path(client: TmdbClient, con: sqlite3.Connection, chosen: dict) -> str | None:
    """Bei fehlendem Poster in der Find-Antwort die Detail-Endpunkte probieren."""
    if chosen.get("poster_path"):
        return str(chosen["poster_path"])
    if not chosen.get("tmdb_id"):
        return None
    media_type = chosen["media_type"]
    for language in (client.cfg.poster_language, "en-US"):
        payload = client.details(media_type, int(chosen["tmdb_id"]), language)
        _cache_payload(con, f"{media_type}:{chosen['tmdb_id']}:{language}", payload)
        if payload.get("poster_path"):
            return str(payload["poster_path"])
    return None


def _upsert_poster(con: sqlite3.Connection, tconst: str, *, status: str,
                   chosen: dict | None = None, image_file: str | None = None,
                   error: str | None = None) -> None:
    con.execute(
        "INSERT INTO posters (tconst, tmdb_id, media_type, poster_path, image_file, status, error, fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(tconst) DO UPDATE SET tmdb_id=COALESCE(excluded.tmdb_id, posters.tmdb_id), "
        "media_type=COALESCE(excluded.media_type, posters.media_type), "
        "poster_path=COALESCE(excluded.poster_path, posters.poster_path), "
        "image_file=COALESCE(excluded.image_file, posters.image_file), status=excluded.status, "
        "error=excluded.error, fetched_at=excluded.fetched_at",
        (tconst, (chosen or {}).get("tmdb_id"), (chosen or {}).get("media_type"),
         (chosen or {}).get("poster_path"), image_file, status, error, _now()),
    )


def _skip(cfg: Config, row: sqlite3.Row | None) -> str | None:
    if row is None:
        return None
    status = row["status"]
    age = _age_days(row["fetched_at"])
    if status == "ok":
        image_file = row["image_file"]
        if image_file and (cfg.posters_dir / image_file).exists():
            if age < cfg.poster_refresh_days:
                return "fresh"
            return None
        return None
    if status == "missing" and age < cfg.poster_missing_retry_days:
        return "missing"
    if status == "error" and age < cfg.poster_error_retry_days:
        return "error"
    return None


def _purge_old_cache(con: sqlite3.Connection, cfg: Config) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, cfg.poster_refresh_days))
    con.execute("DELETE FROM tmdb_cache WHERE fetched_at < ?", (cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),))
    con.commit()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def poster_stats(con: sqlite3.Connection) -> dict:
    counts = {r[0]: r[1] for r in con.execute("SELECT status, count(*) FROM posters GROUP BY status")}
    last = con.execute("SELECT max(fetched_at) FROM posters").fetchone()[0]
    return {"ok": counts.get("ok", 0), "missing": counts.get("missing", 0),
            "error": counts.get("error", 0), "last": last}


def fetch_posters(con: sqlite3.Connection, cfg: Config, *, force: bool = False,
                  limit: int | None = None, prune: bool = False,
                  log=print, client: TmdbClient | None = None) -> dict:
    if not cfg.poster_enabled:
        return {"enabled": False, "total": 0, "skipped": 0, "ok": 0, "missing": 0,
                "error": 0, "downloaded": 0, "removed": 0}

    rows = con.execute(
        "SELECT DISTINCT sm.tconst, t.title_type FROM show_match sm "
        "LEFT JOIN imdb.titles t ON t.tconst = sm.tconst "
        "WHERE sm.tconst IS NOT NULL AND sm.tconst != '' ORDER BY sm.tconst"
    ).fetchall()
    title_types = {r["tconst"]: r["title_type"] for r in rows}
    matched = list(title_types)
    known = {r["tconst"]: r for r in con.execute("SELECT * FROM posters").fetchall()}

    stats = {"enabled": True, "total": len(matched), "skipped": 0, "ok": 0,
             "missing": 0, "error": 0, "downloaded": 0, "removed": 0}
    if not matched:
        return stats

    cfg.posters_dir.mkdir(parents=True, exist_ok=True)
    tmdb = client or TmdbClient(cfg)
    attempted = 0
    try:
        try:
            _cache_payload(con, "configuration", tmdb.configuration())
            con.commit()
        except Exception as exc:  # noqa: BLE001 - Fallback auf bekannte CDN-Basis
            log(f"TMDB-Konfiguration nicht geladen: {exc}")

        for tconst in matched:
            row = known.get(tconst)
            skip = _skip(cfg, row)
            if not force and skip:
                stats["skipped"] += 1
                continue
            if limit is not None and attempted >= limit:
                break
            attempted += 1

            chosen: dict | None = None
            try:
                payload = tmdb.find(tconst)
                _cache_payload(con, f"find:{tconst}:{cfg.poster_language}", payload)
                chosen = _choose(payload, title_types.get(tconst))
                if chosen is None:
                    _upsert_poster(con, tconst, status="missing")
                    stats["missing"] += 1
                    con.commit()
                    continue

                poster_path = _poster_path(tmdb, con, chosen)
                if not poster_path:
                    _upsert_poster(con, tconst, chosen=chosen, status="missing")
                    stats["missing"] += 1
                    con.commit()
                    continue

                image_file = f"{tconst}.jpg"
                data = tmdb.download(tmdb.image_url(poster_path))
                _write_atomic(cfg.posters_dir / image_file, data)
                chosen["poster_path"] = poster_path
                _upsert_poster(con, tconst, chosen=chosen, status="ok", image_file=image_file)
                stats["ok"] += 1
                stats["downloaded"] += 1
                con.commit()
            except Exception as exc:  # noqa: BLE001 - ein Titel darf den Lauf nicht stoppen
                stats["error"] += 1
                _upsert_poster(con, tconst, chosen=chosen, status="error", error=str(exc))
                con.commit()
                log(f"{tconst}: {type(exc).__name__}: {exc}")
            else:
                if stats["downloaded"] and stats["downloaded"] % 25 == 0:
                    log(f"  {stats['downloaded']} Poster geladen")
    finally:
        if client is None:
            tmdb.close()

    if prune:
        keep = set(matched)
        for row in con.execute("SELECT tconst, image_file FROM posters").fetchall():
            if row["tconst"] in keep:
                continue
            if row["image_file"]:
                try:
                    (cfg.posters_dir / row["image_file"]).unlink()
                except FileNotFoundError:
                    pass
            con.execute("DELETE FROM posters WHERE tconst = ?", (row["tconst"],))
            stats["removed"] += 1
        con.commit()

    _purge_old_cache(con, cfg)
    return stats


def run_posters_job(cfg: Config, log=print, *, prune: bool = False) -> str:
    from .db import connect

    con = connect(cfg)
    try:
        stats = fetch_posters(con, cfg, prune=prune, log=log)
    finally:
        con.close()
    return (f"posters: {stats['downloaded']} geladen, {stats['missing']} ohne Poster, "
            f"{stats['error']} Fehler, {stats['skipped']} uebersprungen")
