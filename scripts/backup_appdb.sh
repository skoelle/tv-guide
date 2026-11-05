#!/usr/bin/env bash
# Sichert data/app.db konsistent per SQLite-Backup-API (WAL-sicher).
#
#   ./scripts/backup_appdb.sh [ZIELORDNER]   (Default: backup)
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="${1:-backup}"
PYTHON="${PYTHON:-python3}"
mkdir -p "$DEST"

"$PYTHON" - "$DEST" <<'PY'
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

dest = Path(sys.argv[1])
src_path = Path("data/app.db")
if not src_path.exists():
    sys.exit("data/app.db fehlt")
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
out_path = dest / f"app_{stamp}.db"
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
out = sqlite3.connect(out_path)
try:
    src.backup(out)
finally:
    out.close()
    src.close()
print(f"Backup: {out_path}")
PY
