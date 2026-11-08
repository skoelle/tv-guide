"""Datenstand sammeln. Wird vom CLI (status) und von der Webseite (/status) genutzt."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__
from .config import Config
from .db import Db
from .posters import poster_stats
from .review import review_count, review_group_count


def _one(con: sqlite3.Connection, sql: str, params: tuple = ()):
    try:
        row = con.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row is not None else None


def _file_info(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False}
    st = path.stat()
    return {"path": str(path), "exists": True, "size": st.st_size, "mtime": int(st.st_mtime)}


def collect(con: Db, cfg: Config) -> dict:
    epg_ok = con.attached.get("epg") in ("ok", "immutable")
    imdb_ok = con.attached.get("imdb") in ("ok", "immutable")

    epg: dict = {"state": con.attached.get("epg"), "file": _file_info(cfg.epg_db),
                 "channels": None, "events": None, "first_start": None, "last_stop": None,
                 "last_import": None, "last_changed_import": None}
    if epg_ok:
        epg.update(
            channels=_one(con, "SELECT count(*) FROM epg.channels"),
            events=_one(con, "SELECT count(*) FROM epg.events"),
            first_start=_one(con, "SELECT min(start_ts) FROM epg.events"),
            last_stop=_one(con, "SELECT max(stop_ts) FROM epg.events"),
        )
        last = last_changed = None
        try:
            last = con.execute(
                "SELECT finished_at, source, events_total, events_new, events_changed, events_pruned "
                "FROM epg.imports WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_changed = con.execute(
                "SELECT finished_at, source, events_total, events_new, events_changed, events_pruned "
                "FROM epg.imports WHERE finished_at IS NOT NULL "
                "AND (events_new > 0 OR events_changed > 0 OR events_pruned > 0) "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        except sqlite3.Error:
            pass
        epg["last_import"] = dict(last) if last else None
        epg["last_changed_import"] = dict(last_changed) if last_changed else None

    imdb: dict = {"state": con.attached.get("imdb"), "file": _file_info(cfg.imdb_db),
                  "titles": None, "names": None, "episodes": None,
                  "credits": None, "people": None, "meta": {}}
    if imdb_ok:
        imdb.update(
            titles=_one(con, "SELECT count(*) FROM imdb.titles"),
            names=_one(con, "SELECT count(*) FROM imdb.title_names"),
            episodes=_one(con, "SELECT count(*) FROM imdb.episodes"),
            credits=_one(con, "SELECT count(*) FROM imdb.credits"),
            people=_one(con, "SELECT count(*) FROM imdb.people"),
        )
        try:
            imdb["meta"] = {r[0]: r[1] for r in con.execute("SELECT key, value FROM imdb.meta")}
        except sqlite3.Error:
            imdb["meta"] = {}

    by_status = {}
    for r in con.execute("SELECT status, count(*) AS n FROM show_match GROUP BY status"):
        by_status[r["status"]] = r["n"]
    app = {
        "file": _file_info(cfg.app_db),
        "schema_version": _one(con, "SELECT version FROM schema_version"),
        "shows": _one(con, "SELECT count(*) FROM epg_shows"),
        "matches": by_status,
        "overrides": _one(con, "SELECT count(*) FROM overrides"),
        "searches": _one(con, "SELECT count(*) FROM saved_searches"),
        "review_open": review_count(con),
        "review_open_groups": review_group_count(con, cfg),
        "posters": poster_stats(con),
    }

    return {
        "version": __version__,
        "config_file": str(cfg.source) if cfg.source else None,
        "timezone": cfg.timezone,
        "epg": epg,
        "imdb": imdb,
        "app": app,
        "problems": problems(epg, imdb),
    }


def problems(epg: dict, imdb: dict) -> list[str]:
    out = []
    for name, d in (("epg.db", epg), ("imdb.db", imdb)):
        if d["state"] == "missing":
            out.append(f"{name} fehlt: {d['file']['path']}")
        elif d["state"] and str(d["state"]).startswith("error"):
            out.append(f"{name} nicht lesbar: {d['state']}")
    return out


def fmt_ts(ts: int | None, tz: str, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(int(ts), ZoneInfo(tz)).strftime(fmt)


def fmt_iso(value: str | None, tz: str, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz)).strftime(fmt)


def fmt_num(n) -> str:
    if n is None:
        return "-"
    return f"{int(n):,}".replace(",", ".")


def fmt_size(n: int | None) -> str:
    if n is None:
        return "-"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return str(n)


def render_text(s: dict) -> str:
    tz = s["timezone"]
    L = [f"tvguide {s['version']}  (Konfiguration: {s['config_file'] or 'Standardwerte'})", ""]
    e, i, a = s["epg"], s["imdb"], s["app"]

    L.append(f"EPG ({e['file']['path']}) [{e['state']}]")
    if e["file"]["exists"]:
        L.append(f"  Datei:      {fmt_size(e['file'].get('size'))}, geaendert {fmt_ts(e['file'].get('mtime'), tz)}")
    if e.get("events") is not None:
        L.append(f"  Sender:     {fmt_num(e['channels'])}")
        L.append(f"  Sendungen:  {fmt_num(e['events'])}")
        L.append(f"  Zeitraum:   {fmt_ts(e['first_start'], tz)} bis {fmt_ts(e['last_stop'], tz)}")
        li = e.get("last_import")
        if li:
            L.append(f"  Letzter Import: {fmt_iso(li['finished_at'], tz)} "
                     f"({fmt_num(li['events_new'])} neu, {fmt_num(li['events_changed'])} geaendert, "
                     f"{fmt_num(li['events_pruned'])} entfernt)")
        lc = e.get("last_changed_import")
        if lc:
            L.append(f"  Letzter Import mit Aenderungen: {fmt_iso(lc['finished_at'], tz)} "
                     f"({fmt_num(lc['events_new'])} neu, {fmt_num(lc['events_changed'])} geaendert, "
                     f"{fmt_num(lc['events_pruned'])} entfernt)")
        else:
            L.append("  Letzter Import mit Aenderungen: noch keiner")
    L.append("")

    L.append(f"IMDb ({i['file']['path']}) [{i['state']}]")
    if i["file"]["exists"]:
        L.append(f"  Datei:      {fmt_size(i['file'].get('size'))}, geaendert {fmt_ts(i['file'].get('mtime'), tz)}")
    if i.get("titles") is not None:
        L.append(f"  Titel:      {fmt_num(i['titles'])}")
        L.append(f"  Namen:      {fmt_num(i['names'])}")
        L.append(f"  Folgen:     {fmt_num(i.get('episodes'))}")
        L.append(f"  Credits:    {fmt_num(i.get('credits'))} ({fmt_num(i.get('people'))} Personen)")
        m = i.get("meta") or {}
        if m:
            L.append(f"  Datenstand: {m.get('source_date', '-')}, gebaut {m.get('built_at', '-')}")
    L.append("")

    L.append(f"App ({a['file']['path']})")
    L.append(f"  Schema-Version: {a['schema_version']}")
    L.append(f"  Shows: {fmt_num(a['shows'])}, Overrides: {fmt_num(a['overrides'])}, gespeicherte Suchen: {fmt_num(a['searches'])}")
    if a["matches"]:
        L.append("  Zuordnungen: " + ", ".join(f"{k} {fmt_num(v)}" for k, v in sorted(a["matches"].items())))
    else:
        L.append("  Zuordnungen: noch keine (Matching folgt in M2)")
    p = a.get("posters") or {}
    L.append(f"  Poster: {fmt_num(p.get('ok'))} ok, {fmt_num(p.get('missing'))} fehlend, "
             f"{fmt_num(p.get('error'))} Fehler" + (f", zuletzt {p.get('last')}" if p.get("last") else ""))
    if s["problems"]:
        L.append("")
        L.append("Probleme:")
        L.extend(f"  - {p}" for p in s["problems"])
    return "\n".join(L)
