#!/usr/bin/env bash
# Holt Daten von der Synology und laesst den Container matchen/benachrichtigen.
# Fuer Cron / Synology Task Scheduler auf dem Docker-Host.
#
#   TVGUIDE_SYNC_HOST=stefan@synology ./scripts/docker_update.sh
#   ./scripts/docker_update.sh epg.db imdb.db
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -eq 0 ]; then
  set -- epg.db
fi

./scripts/sync_data.sh "$@"
docker compose exec -T tvguide python -m tvguide refresh
