#!/usr/bin/env bash
# Startet tvguide. Ohne Argument: Webserver. Beispiele:
#   ./run.sh                 Webserver
#   ./run.sh status          Datenstand im Terminal
#   ./run.sh initdb          app.db anlegen/migrieren
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo ".venv fehlt. Zuerst ./setup.sh ausfuehren." >&2
  exit 1
fi
if [ $# -eq 0 ]; then set -- serve; fi
exec .venv/bin/python -m tvguide "$@"
