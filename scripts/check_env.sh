#!/usr/bin/env bash
# M0: Umgebung pruefen (nur lesend).
cd "$(dirname "$0")/.."
PORT="${1:-8085}"
ok()   { printf '  [ok]   %s\n' "$*"; }
warn() { printf '  [warn] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; }

echo "Python"
PYV=$(python3 -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')
python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)' && ok "python3 $PYV" || bad "python3 $PYV (mindestens 3.11 noetig, wegen tomllib)"
python3 -c "import venv" 2>/dev/null && ok "venv-Modul vorhanden" || bad "python3-venv fehlt: sudo apt install python3-venv"
[ -x .venv/bin/python ] && ok ".venv vorhanden" || warn ".venv fehlt: ./setup.sh ausfuehren"

echo "SQLite"
python3 - <<'PY'
import sqlite3
print("  [ok]   SQLite", sqlite3.sqlite_version)
try:
    c = sqlite3.connect(":memory:"); c.execute("create virtual table t using fts5(x)")
    print("  [ok]   FTS5 verfuegbar")
except Exception as e:
    print("  [FAIL] FTS5 fehlt:", e)
PY

echo "Dateisystem"
if command -v findmnt >/dev/null; then
  FSTYPE=$(findmnt -n -o FSTYPE -T data 2>/dev/null || findmnt -n -o FSTYPE -T .)
  case "$FSTYPE" in
    cifs|smb3|nfs|nfs4|fuse.sshfs) bad "data/ liegt auf Netzlaufwerk ($FSTYPE): SQLite/WAL dort vermeiden" ;;
    *) ok "lokales Dateisystem ($FSTYPE)" ;;
  esac
fi
df -h --output=avail,pcent . | tail -1 | awk '{printf "  [info] frei: %s, belegt: %s\n",$1,$2}'

echo "Daten"
for f in data/epg.db data/imdb.db; do
  if [ -f "$f" ]; then ok "$f ($(du -h "$f" | cut -f1))"; else warn "$f fehlt"; fi
done
[ -f data/app.db ] && ok "data/app.db vorhanden" || warn "data/app.db noch nicht angelegt (./run.sh initdb)"
[ -f config.toml ] && ok "config.toml vorhanden" || warn "config.toml fehlt (./setup.sh legt sie an)"
[ -f channels_ignore.txt ] && ok "channels_ignore.txt vorhanden" || warn "channels_ignore.txt fehlt (wird ab M2 gebraucht)"

echo "Port $PORT"
if ss -tln 2>/dev/null | awk '{print $4}' | grep -q ":$PORT\$"; then bad "Port $PORT ist belegt"; else ok "Port $PORT ist frei"; fi
