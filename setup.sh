#!/usr/bin/env bash
# Legt die virtuelle Umgebung an und installiert die Abhaengigkeiten.
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -c "import venv" 2>/dev/null; then
  echo "python3-venv fehlt. Installation: sudo apt install python3-venv" >&2
  exit 1
fi

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

if [ ! -f config.toml ]; then
  cp config.example.toml config.toml
  echo "config.toml aus config.example.toml erzeugt (anpassen mit: vim config.toml)"
fi
mkdir -p data
echo "Fertig. Pruefen mit: ./scripts/check_env.sh   Starten mit: ./run.sh"
