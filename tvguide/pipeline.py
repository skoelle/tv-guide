"""Datenpipeline: EPG-Import und IMDb-Build im Container, danach Refresh.

Die Werkzeuge in `tools/` bleiben unveraendert und werden als Subprozess aufgerufen.
Nach einem Import/Build laeuft `refresh` mit einer frischen Verbindung, damit die
neue Datenbank angehaengt wird.
"""
from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

from .config import Config

ROOT = Path(__file__).resolve().parent.parent
MIN_FREE_BYTES = 4 * 1024**3  # Reserve fuer imdb_raw (inkl. Cast/Crew) + Temp-Build + fertige DB


def _run_stream(cmd: list[str], log, display: list[str] | None = None) -> None:
    log("$ " + " ".join(display or cmd))
    with subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, bufsize=1) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            log(line.rstrip("\n"))
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"command failed with exit code {rc}")


def _latest_epg_sha1(path: Path) -> str | None:
    if not path.exists():
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT xml_sha1 FROM imports WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _refresh(cfg: Config, log) -> str:
    from .db import connect
    from .refresh import refresh

    con = connect(cfg)
    try:
        stats = refresh(con, cfg)
    finally:
        con.close()
    if stats.get("changed"):
        by = stats.get("by_status", {})
        msg = (f"refresh: auto {by.get('auto', 0)}, review {by.get('review', 0)}, "
               f"none {by.get('none', 0)}")
    else:
        msg = "refresh: unchanged"
    log(msg)
    return msg


def run_epg_job(cfg: Config, log=print) -> str:
    """Importiert das TVHeadend-XMLTV in `epg.db` und prueft danach den Refresh.

    Auch bei unveraendertem EPG wird refresht, damit Aenderungen an der
    Ignorierliste oder anderen Eingaben erkannt werden.
    """
    epg = cfg.epg_db
    old_sha = _latest_epg_sha1(epg)
    cmd = [sys.executable, str(ROOT / "tools" / "epg_import.py"), "--db", str(epg), "--url", cfg.epg_url]
    display = list(cmd)
    if cfg.epg_user:
        cmd += ["--user", cfg.epg_user]
        display += ["--user", cfg.epg_user]
        password = os.environ.get("TVGUIDE_TVH_PASSWORD", "")
        if password:
            cmd += ["--password", password]
            display += ["--password", "***"]
    if cfg.epg_prune_days:
        cmd += ["--prune-days", str(cfg.epg_prune_days)]
        display += ["--prune-days", str(cfg.epg_prune_days)]
    _run_stream(cmd, log, display)

    new_sha = _latest_epg_sha1(epg)
    if new_sha is None:
        raise RuntimeError("EPG import produced no data")
    if new_sha == old_sha:
        log("EPG unchanged")
        return "EPG unchanged; " + _refresh(cfg, log)
    log(f"EPG changed ({str(new_sha)[:8]}), refreshing")
    return "EPG imported; " + _refresh(cfg, log)


def _check_disk(path: Path) -> None:
    free = shutil.disk_usage(path).free
    if free < MIN_FREE_BYTES:
        raise RuntimeError(
            f"not enough free disk space for the IMDb build: {free / 1e9:.1f} GB free, "
            f"about {MIN_FREE_BYTES / 1e9:.0f} GB needed")


def _posters(cfg: Config, log) -> str:
    if not cfg.poster_enabled:
        return ""
    from .posters import run_posters_job

    try:
        return run_posters_job(cfg, log=log, prune=True)
    except Exception as exc:  # noqa: BLE001 - Poster duerfen den IMDb-Job nicht scheitern lassen
        msg = f"posters: failed ({type(exc).__name__}: {exc})"
        log(msg)
        return msg


def _matched_tconsts(cfg: Config) -> set[str]:
    """Aktuell gematchte IMDb-IDs aus app.db (nur lesen, keine Migration)."""
    if not cfg.app_db.exists():
        return set()
    con = sqlite3.connect(f"file:{cfg.app_db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT tconst FROM show_match WHERE tconst IS NOT NULL AND tconst != ''"
        ).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        con.close()
    return {str(r[0]) for r in rows}


def run_imdb_job(cfg: Config, log=print) -> str:
    """Baut `imdb.db` aus den IMDb-Datasets, refresht und holt fehlende Poster."""
    raw = cfg.imdb_raw_dir or (cfg.app_db.parent / "imdb_raw")
    raw.mkdir(parents=True, exist_ok=True)
    _check_disk(raw)

    cmd = [sys.executable, str(ROOT / "tools" / "imdb_build.py"),
           "--db", str(cfg.imdb_db), "--raw-dir", str(raw), "--types", cfg.imdb_types]
    if cfg.imdb_min_votes:
        cmd += ["--min-votes", str(cfg.imdb_min_votes)]
    if cfg.imdb_min_year:
        cmd += ["--min-year", str(cfg.imdb_min_year)]

    extras = cfg.imdb_with_episodes or cfg.imdb_with_crew or cfg.imdb_with_cast
    tconst_file = None
    scope = _matched_tconsts(cfg) if extras else set()
    if extras and not scope:
        log("Keine gematchten Titel vorhanden; Crew/Cast/Episoden werden uebersprungen")
    elif extras:
        tconst_file = raw / ".matched_tconsts.txt"
        tconst_file.write_text("\n".join(sorted(scope)) + "\n", encoding="utf-8")
        cmd += ["--tconst-file", str(tconst_file)]
        if cfg.imdb_with_episodes:
            cmd += ["--with-episodes"]
        if cfg.imdb_with_crew:
            cmd += ["--with-crew"]
        if cfg.imdb_with_cast:
            cmd += ["--with-cast", "--cast-limit", str(cfg.imdb_cast_limit)]

    log(f"host={socket.gethostname()} free={shutil.disk_usage(raw).free / 1e9:.1f} GB")
    try:
        _run_stream(cmd, log)
    finally:
        if tconst_file is not None:
            try:
                tconst_file.unlink()
            except FileNotFoundError:
                pass
    message = "IMDb build done; " + _refresh(cfg, log)
    posters = _posters(cfg, log)
    return f"{message}; {posters}" if posters else message


def run_refresh_job(cfg: Config, log=print) -> str:
    return _refresh(cfg, log)
