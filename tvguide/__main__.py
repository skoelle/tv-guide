"""Kommandozeile: python -m tvguide <status|initdb|serve|config>."""
from __future__ import annotations

import argparse
import json
import sys

from .config import load_config
from .db import connect
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
