#!/usr/bin/env bash
# Holt Datenbanken von der Synology atomar nach data/.
#
#   TVGUIDE_SYNC_HOST=stefan@synology \
#   TVGUIDE_SYNC_SRC=/volume1/work/Xchange/imdb \
#   ./scripts/sync_data.sh epg.db [imdb.db ...]
#
# Ohne Dateiargument wird epg.db geholt. Jede Datei wird in eine Temp-Datei
# kopiert und erst danach per mv ausgetauscht, damit die App nie eine halbe
# Datei sieht. Vorher wird auf der Quelle (best effort) ein WAL-Checkpoint
# ausgefuehrt, sofern TVGUIDE_SYNC_CHECKPOINT=1 gesetzt ist.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${TVGUIDE_SYNC_HOST:-}"
SRC="${TVGUIDE_SYNC_SRC:-/volume1/work/Xchange/imdb}"
CHECKPOINT="${TVGUIDE_SYNC_CHECKPOINT:-1}"

if [ -z "$HOST" ]; then
  echo "TVGUIDE_SYNC_HOST ist nicht gesetzt (z.B. stefan@synology)." >&2
  exit 1
fi

FILES=("$@")
[ ${#FILES[@]} -eq 0 ] && FILES=(epg.db)
mkdir -p data

for f in "${FILES[@]}"; do
  if [ "$CHECKPOINT" = "1" ]; then
    ssh "$HOST" "command -v sqlite3 >/dev/null && sqlite3 '$SRC/$f' 'PRAGMA wal_checkpoint(TRUNCATE);' || true" || true
  fi
  tmp="data/.${f}.tmp.$$"
  echo "Hole $HOST:$SRC/$f"
  rsync --partial --inplace --no-owner --no-group "$HOST:$SRC/$f" "$tmp"
  mv -f "$tmp" "data/$f"
  echo "  -> data/$f ($(du -h "data/$f" | cut -f1))"
done

echo "Fertig. Danach: ./run.sh refresh"
