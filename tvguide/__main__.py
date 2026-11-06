"""Kommandozeile: python -m tvguide <status|initdb|serve|config>."""
from __future__ import annotations

import argparse
import json
import sys

from .config import load_config
from .db import connect
from .matching import run_match
from .notify.digest import send_notifications, send_test
from .pipeline import run_epg_job, run_imdb_job
from .posters import PosterError, fetch_posters, poster_stats
from .refresh import refresh
from .search import run_searches
from .status import collect, render_text


def cmd_status(args, cfg) -> int:
    con = connect(cfg)
    try:
        data = collect(con, cfg)
    finally:
        con.close()
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str) if args.json else render_text(data))
    return 1 if data["problems"] else 0


def cmd_initdb(args, cfg) -> int:
    con = connect(cfg)
    version = con.execute("SELECT version FROM schema_version").fetchone()[0]
    con.close()
    print(f"app.db bereit: {cfg.app_db} (Schema-Version {version})")
    return 0


def cmd_config(args, cfg) -> int:
    print(f"Konfiguration: {cfg.source or 'Standardwerte (keine config.toml gefunden)'}")
    for key in ("epg_db", "imdb_db", "app_db", "ignore_channels_file"):
        print(f"  {key:22} {getattr(cfg, key)}")
    print(f"  {'host:port':22} {cfg.host}:{cfg.port}")
    print(f"  {'timezone':22} {cfg.timezone}")
    print(f"  {'schwellen':22} auto >= {cfg.auto_threshold}, review >= {cfg.review_threshold}")
    print(f"  {'retro':22} {cfg.retro_from} bis {cfg.retro_to}, Serienende bis {cfg.retro_end_max}")
    print(f"  {'skip':22} {len(cfg.skip_categories)} Kategorien, {len(cfg.skip_titles)} Titel, "
          f"kurz < {cfg.skip_min_duration} min, Block >= {cfg.skip_block_min_duration} min / {cfg.skip_block_min_events}x")
    print(f"  {'epg':22} {cfg.epg_url}")
    print(f"  {'imdb raw':22} {cfg.imdb_raw_dir}")
    print(f"  {'schedule':22} {'an' if cfg.schedule_enabled else 'aus'}, EPG alle "
          f"{cfg.schedule_epg_every_minutes} min, IMDb Wochentag {cfg.schedule_imdb_weekday} um {cfg.schedule_imdb_time}")
    return 0


def cmd_epg(args, cfg) -> int:
    print(run_epg_job(cfg, log=print))
    return 0


def cmd_imdb(args, cfg) -> int:
    print(run_imdb_job(cfg, log=print))
    return 0


def cmd_match(args, cfg) -> int:
    con = connect(cfg)
    try:
        def progress(done: int, total: int) -> None:
            print(f"  {done}/{total} Shows", file=sys.stderr)
        stats = run_match(con, cfg, progress=progress)
    finally:
        con.close()
    by = stats["by_status"]
    print(f"Matching fertig in {stats['seconds']:.1f}s")
    print(f"  Events: {stats['events_total']} gesamt, {stats['events_ignored']} ignorierte Sender, "
          f"{stats['events_considered']} betrachtet")
    print(f"  Shows:  {stats['shows']}")
    for key in ("auto", "review", "none"):
        print(f"  {key:8} {by.get(key, 0)}")
    print(f"  {'skip':8} {sum(v for k, v in by.items() if k.startswith('skip'))}")
    return 0


def cmd_refresh(args, cfg) -> int:
    con = connect(cfg)
    try:
        def progress(done: int, total: int) -> None:
            print(f"  {done}/{total} Shows", file=sys.stderr)
        stats = refresh(con, cfg, force=args.force, progress=progress)
    finally:
        con.close()
    if not stats["changed"]:
        print("epg.db unveraendert, kein neuer Match-Lauf.")
        print(f"  Shows: {stats['shows']}, Events: {stats['events']}")
        if stats["by_status"]:
            print("  Zuordnungen: " + ", ".join(f"{k} {v}" for k, v in sorted(stats["by_status"].items())))
        return 0
    by = stats["by_status"]
    print(f"Refresh fertig in {stats['seconds']:.1f}s ({stats['new_events']} neue Sendungen im letzten Import)")
    print(f"  Shows:  {stats['shows']} (auto {by.get('auto', 0)}, review {by.get('review', 0)}, none {by.get('none', 0)})")
    return 0


def cmd_posters(args, cfg) -> int:
    con = connect(cfg)
    try:
        try:
            stats = fetch_posters(con, cfg, force=args.force, limit=args.limit,
                                  prune=args.prune, log=print)
        except PosterError as exc:
            print(f"Poster-Abruf nicht moeglich: {exc}", file=sys.stderr)
            return 2
        state = poster_stats(con)
    finally:
        con.close()
    print(f"Poster: {stats['downloaded']} geladen, {stats['missing']} ohne Poster, "
          f"{stats['error']} Fehler, {stats['skipped']} uebersprungen, "
          f"{stats['removed']} entfernt")
    print(f"Bestand: {state['ok']} ok, {state['missing']} fehlend, {state['error']} Fehler"
          + (f", zuletzt {state['last']}" if state["last"] else ""))
    return 0


def cmd_searches(args, cfg) -> int:
    con = connect(cfg)
    try:
        stats = run_searches(con, cfg)
    finally:
        con.close()
    if not stats:
        print("Keine gespeicherten Suchen.")
        return 0
    for s in stats:
        print(f"  {s['name']}: {s['hits']} Treffer, {s['new']} neu")
    return 0


def cmd_notify(args, cfg) -> int:
    if args.test:
        result = send_test(cfg, dry_run=args.dry_run)
        print(f"Testmail: {result}")
        return 0
    con = connect(cfg)
    try:
        result = send_notifications(con, cfg, dry_run=args.dry_run)
    finally:
        con.close()
    if result.get("skipped"):
        print(f"Keine Mail gesendet: {result['skipped']} ({result['pending']} offene Treffer).")
    elif result.get("dry_run"):
        print(f"{result['pending']} offene Treffer als {result['file']} geschrieben (nicht versendet).")
    elif result.get("error"):
        print(f"Versand fehlgeschlagen: {result['error']} ({result['pending']} offene Treffer bleiben).")
    else:
        print(f"{result['sent']} Treffer in einer Mail versendet.")
    return 0


def cmd_serve(args, cfg) -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn fehlt. Zuerst ./setup.sh ausfuehren.", file=sys.stderr)
        return 1
    connect(cfg).close()  # legt app.db an und migriert vor dem Start
    host = args.host or cfg.host
    port = args.port or cfg.port
    print(f"tvguide laeuft auf http://{host}:{port}/ (Strg+C beendet)")
    uvicorn.run("tvguide.web.app:app", host=host, port=port, reload=args.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS,
                        help="Pfad zur config.toml (sonst TVGUIDE_CONFIG oder ./config.toml)")

    ap = argparse.ArgumentParser(prog="tvguide", description="TV-Guide", parents=[common])
    sub = ap.add_subparsers(dest="command")

    p = sub.add_parser("status", parents=[common], help="Datenstand anzeigen")
    p.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("initdb", parents=[common], help="app.db anlegen und migrieren")
    p.set_defaults(func=cmd_initdb)

    p = sub.add_parser("config", parents=[common], help="geladene Konfiguration anzeigen")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("match", parents=[common], help="EPG-Shows gegen IMDb matchen")
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("refresh", parents=[common], help="geaenderte epg.db erkennen und matchen")
    p.add_argument("--force", action="store_true", help="Match auch ohne erkannte Aenderung ausfuehren")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("posters", parents=[common], help="Poster von TMDB laden und cachen")
    p.add_argument("--limit", type=int, help="hoechstens so viele Titel in diesem Lauf abfragen")
    p.add_argument("--force", action="store_true", help="auch frische Poster neu laden")
    p.add_argument("--prune", action="store_true", help="Bilder nicht mehr zugeordneter Titel entfernen")
    p.set_defaults(func=cmd_posters)

    p = sub.add_parser("searches", parents=[common], help="gespeicherte Suchen auswerten (search_hits)")
    p.set_defaults(func=cmd_searches)

    p = sub.add_parser("epg", parents=[common], help="TVHeadend-XMLTV importieren und refreshen")
    p.set_defaults(func=cmd_epg)

    p = sub.add_parser("imdb", parents=[common], help="IMDb-Datasets neu bauen und refreshen")
    p.set_defaults(func=cmd_imdb)

    p = sub.add_parser("notify", parents=[common], help="Benachrichtigung ueber neue Treffer versenden")
    p.add_argument("--dry-run", action="store_true", help="Mail als .eml nach data/outbox/ schreiben")
    p.add_argument("--test", action="store_true", help="Testmail senden")
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("serve", parents=[common], help="Webserver starten")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--reload", action="store_true", help="Auto-Reload (Entwicklung)")
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args(argv)
    if not args.command:
        args = ap.parse_args(["serve"] + (argv or []))
    config_path = getattr(args, "config", None)
    try:
        cfg = load_config(config_path)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    if config_path:
        import os
        os.environ["TVGUIDE_CONFIG"] = str(cfg.source)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
