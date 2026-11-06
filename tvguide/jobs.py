"""Hintergrundjobs fuer EPG-Import und IMDb-Build.

Genau ein Job gleichzeitig. Der Job-Status liegt in `app.db` (Tabelle `jobs`),
das Log unter `data/logs/job_<id>.log`. Scheduler und die Buttons in /status
nutzen denselben Runner.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

KINDS = ("epg", "imdb", "posters", "refresh")
_LOG_TAIL_BYTES = 4000
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def running_job(con: sqlite3.Connection):
    return con.execute("SELECT * FROM jobs WHERE status = 'running' ORDER BY id DESC LIMIT 1").fetchone()


def get_job(con: sqlite3.Connection, job_id: int):
    return con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def _tail(path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _LOG_TAIL_BYTES))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def recent_jobs(con: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = con.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["log_tail"] = _tail(d.get("log_path"))
        out.append(d)
    return out


def recover_stale(con: sqlite3.Connection) -> int:
    """Markiert nach einem Neustart haengende Jobs als abgebrochen."""
    cur = con.execute(
        "UPDATE jobs SET status='error', finished_at=?, message='interrupted (container restart)' "
        "WHERE status='running'", (_now(),))
    con.commit()
    return cur.rowcount


def run_kind(cfg: Config, kind: str, log) -> str:
    """Fuehrt die eigentliche Arbeit aus (separat, damit Tests es ersetzen koennen)."""
    from . import pipeline

    if kind == "epg":
        return pipeline.run_epg_job(cfg, log=log)
    if kind == "imdb":
        return pipeline.run_imdb_job(cfg, log=log)
    if kind == "posters":
        from .posters import run_posters_job
        return run_posters_job(cfg, log=log)
    return pipeline.run_refresh_job(cfg, log=log)


def _run_thread(cfg: Config, job_id: int, kind: str, log_path: str) -> None:
    status, message = "ok", ""
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            def log(line: str = "") -> None:
                f.write(str(line).rstrip("\n") + "\n")
                f.flush()

            message = run_kind(cfg, kind, log) or ""
    except Exception as exc:  # noqa: BLE001 - Job-Fehler gehoeren in den Status
        status = "error"
        message = f"{type(exc).__name__}: {exc}"
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\nERROR: {message}\n")
        except OSError:
            pass
    finally:
        from .db import connect

        try:
            con = connect(cfg)
            try:
                con.execute("UPDATE jobs SET status=?, finished_at=?, message=? WHERE id=?",
                            (status, _now(), message, job_id))
                con.commit()
            finally:
                con.close()
        except Exception:  # noqa: BLE001 - niemand faengt das mehr ab
            pass


def start_job(con: sqlite3.Connection, cfg: Config, kind: str) -> dict:
    """Startet einen Job, wenn keiner laeuft. Gibt {started, id|error} zurueck."""
    if kind not in KINDS:
        return {"started": False, "error": "unknown kind"}
    with _LOCK:
        if running_job(con) is not None:
            return {"started": False, "error": "busy"}
        log_dir = cfg.app_db.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        cur = con.execute("INSERT INTO jobs (kind, status, started_at) VALUES (?,?,?)",
                          (kind, "running", _now()))
        job_id = cur.lastrowid
        log_path = str(log_dir / f"job_{job_id}.log")
        con.execute("UPDATE jobs SET log_path=? WHERE id=?", (log_path, job_id))
        con.commit()
    threading.Thread(target=_run_thread, args=(cfg, job_id, kind, log_path), daemon=True).start()
    return {"started": True, "id": job_id}
