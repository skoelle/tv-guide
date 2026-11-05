# TV-Guide

[![build](https://github.com/skoelle/tv-guide/actions/workflows/build.yml/badge.svg)](https://github.com/skoelle/tv-guide/actions/workflows/build.yml)

Browse the TVHeadend EPG, find movies and series (80s and 90s), and keep saved searches with email notifications.
Details: `SPEC.md` (what) and `PLAN.md` (how). This package covers milestones M0–M8.

## Requirements

- Python 3.11 or newer (tested with 3.12.3), `python3-venv`
- `data/epg.db` and `data/imdb.db` (built by `tools/epg_import.py` and `tools/imdb_build.py`)
- `tools/` with the three scripts is already in the repo and stays unchanged

## Quick start

```bash
./setup.sh                  # create .venv, install dependencies, create config.toml
vim config.toml             # check paths and port
./scripts/check_env.sh      # verify the environment (M0)
./run.sh initdb             # create app.db
./run.sh status             # show data status in the terminal
./run.sh                    # start the web server, then open http://xubuntu-dev:8085/status
```

## Commands

| Command | Effect |
|---|---|
| `./run.sh` or `./run.sh serve` | Web server (port from `config.toml`) |
| `./run.sh status [--json]` | Status of EPG, IMDb and app data |
| `./run.sh match` | Match EPG shows against IMDb and write to `app.db` |
| `./run.sh refresh [--force]` | Detect a changed `epg.db` and only then re-match |
| `./run.sh searches` | Evaluate saved searches (`search_hits`) |
| `./run.sh notify [--dry-run] [--test]` | Send notifications for new hits |
| `scripts/sync_data.sh epg.db` | Fetch `epg.db` from the Synology atomically |
| `./run.sh initdb` | Create and migrate `app.db` |
| `./run.sh config` | Show the loaded configuration |
| `.venv/bin/python -m pytest -q` | Tests |

Use `--config /path/config.toml` (before or after the command) or `TVGUIDE_CONFIG` to select a different configuration.

## Web views

| Path | Content |
|---|---|
| `/` | Now: "running now" and "tonight" with a filter bar |
| `/browse` | Browse: cards per work, filter chips, sorting, pagination |
| `/work/{tconst}` | Work detail with all airings |
| `/review` | Review open matches: confirm, reject, pick another title, undo |
| `/searches` | Saved searches: hits, "new", edit, delete, refresh |
| `/status` | Data status |
| `/api/now`, `/api/browse`, `/api/works/{tconst}`, `/api/review`, `/api/searches`, `/api/status` | JSON |

## Email (since M7)

After every `refresh` (and via `./run.sh notify`) TV-Guide sends **one digest email** for all open hits of searches with notifications enabled. The `[smtp]` section in `config.toml` holds `host`, `port`, `security` (`starttls`|`ssl`), `username`, `from`, `to`; the password comes exclusively from the `TVGUIDE_SMTP_PASSWORD` environment variable. Links in the email use `[server].base_url`. `notified_at` is only set after a successful send – if SMTP is missing or sending fails, the next run retries. `./run.sh notify --dry-run` writes the email to `data/outbox/`, `--test` sends a test message.

Saved searches store a **validated filter JSON** (whitelist: `type`, `decade`, `group`, `genre`, `retro`, `q`, plus `include_review`/`notify`). They are evaluated with the same engine as the filter view, so a search and the view always return the same hits. `search_hits` is updated on `refresh` and via `./run.sh searches`; "new" is the number of airings added during the last run.

Review decisions are stored in `overrides` (they take precedence over the automatic matching) and logged in `override_log` so the last decision can be undone.

Filters are controlled via query parameters (`?type=film&decade=1980,1990&genre=Action&group=retro&status=sicher&retro=1&sort=jahr&q=...`). The page uses a local HTMX build (`static/htmx.min.js`) and still works without JavaScript as a plain form. `?at=<unixtime>` freezes the point in time for tests.

## Docker operation (since M8)

> **Development ≠ operation:** `xubuntu-dev` runs **no container**. It is used for local Python tests only (`./run.sh`, `.venv/bin/python -m pytest -q`). Docker and the GHCR image are only used on the target host (Synology/Docker host).

Requirements: Docker + Compose, an existing `config.toml`, and `data/` with `epg.db`/`imdb.db`.

```bash
cp config.example.toml config.toml      # once; adjust paths/port/SMTP
docker compose up -d --build            # build and start (port 8085)
docker compose logs -f tvguide          # logs
docker compose ps                       # status including healthcheck
```

Volumes: `./config.toml` (read-only) to `/app/config.toml`, `./channels_ignore.txt` (read-only) to `/app/channels_ignore.txt`, and `./data` to `/app/data`. The ignore list must live next to `config.toml` (the path is resolved relative to the configuration file); without it, radio and shopping channels are not filtered out. `app.db` (overrides, searches, hits) lives in the volume and survives restarts and updates. `TVGUIDE_SMTP_PASSWORD` comes from `.env` (not committed); `restart: unless-stopped` restarts the container automatically after a host reboot.

### Pulling the image from GHCR (built in GitHub)

GitHub Actions builds the image on every push to `main` (and weekly) and publishes it as `ghcr.io/skoelle/tv-guide:latest` ([build workflow](https://github.com/skoelle/tv-guide/actions/workflows/build.yml)). You do not need to build anything locally:

```bash
cp docker-compose.example.yml docker-compose.yml   # adjust paths in the volumes
docker compose pull
docker compose up -d
```

The image contains only the code; `config.toml`, `channels_ignore.txt` and `data/` are mounted as volumes. If the GHCR package is still private, set it to **public** under *Packages* in GitHub (or run `docker login ghcr.io` once).

The example compose file sets a Watchtower label for automatic updates. If you configure the `WEBHOOK_URL` (and optionally `WEBHOOK_TOKEN`) repo secrets, the build calls your Watchtower webhook after pushing.

**Updating** (import on the Synology, then fetch data and refresh):

```bash
TVGUIDE_SYNC_HOST=stefan@synology ./scripts/docker_update.sh epg.db
```

This fetches the file atomically into `data/`, runs `refresh` inside the container (match + searches + email), and leaves `app.db` untouched.

**Schedule** (cron on the Docker host or Synology Task Scheduler):

```cron
15 6 * * * cd /path/tv-guide && ./scripts/docker_update.sh epg.db >> data/update.log 2>&1
```

**Backup and restore** (`app.db` is the only non-reproducible state):

```bash
./scripts/backup_appdb.sh                       # -> backup/app_<stamp>.db (WAL-safe)
docker compose stop
cp backup/app_<stamp>.db data/app.db            # remove data/app.db-wal and -shm first
docker compose start
```

`epg.db` and `imdb.db` are reproducible and do not need to be backed up. `backup/`, `data/` and `config.toml` are listed in `.gitignore`.

## Data flow

```
Synology:   epg_import.py -> epg.db        imdb_build.py -> imdb.db
               |  rsync (atomic copy)          |
xubuntu-dev:  data/epg.db (read-only)    data/imdb.db (read-only)
                          \               /
                           tvguide  ->  data/app.db (matches, overrides, searches)
```

`epg.db` and `imdb.db` are never modified by the app. User state lives only in `app.db`.

## Notes

- Keep SQLite files on local filesystems only (no SMB/NFS).
- `*.db`, `data/` and `config.toml` are listed in `.gitignore`.
- IMDb data is for personal, non-commercial use only; attribution is shown in the page footer.

## Manual acceptance M0–M8

- [ ] `./scripts/check_env.sh` shows no `[FAIL]` line.
- [ ] `./run.sh initdb` reports schema version 4.
- [ ] `./run.sh status` shows 195 channels, 49,669 airings and 1,285,899 IMDb titles.
- [ ] `http://xubuntu-dev:8085/status` shows the same numbers in the browser (also from the notebook).
- [ ] `./run.sh match` reports auto 1,425 / review 631 / none 1,556 (runtime under 2 minutes).
- [ ] Afterwards `./run.sh status` shows the same matches and `Shows` in `app.db`.
- [ ] `./run.sh refresh` reports new matches the first time and "unchanged" the second time.
- [ ] `http://xubuntu-dev:8085/` shows The A-Team, MacGyver and the Olsen Gang movies under "Stöbern" with year and airings.
- [ ] Filters (type, decade, channel group, genre, match status, retro) and sorting work; "Läuft gerade" matches the current time.
- [ ] Pages load well under one second (the catalog is cached per process).
- [ ] Under "Review", a suggestion can be confirmed; the show then counts as "sicher" in Now/Browse and survives a refresh.
- [ ] "Letzte Entscheidung rückgängig" restores the previous state.
- [ ] A saved search "Serien der 80er und 90er" returns the same hits as the filter view; after "Treffer jetzt aktualisieren", "new" increases once.
- [ ] `./run.sh notify --dry-run` writes a `.eml` to `data/outbox/`; with SMTP configured, a new hit triggers exactly one email and a second refresh none.
- [ ] `docker compose up -d --build` starts the service; `/healthz` returns `{"ok": true}` and `/status` is reachable.
- [ ] Restarting the container/host preserves overrides and searches (`app.db` in the volume).
- [ ] `./scripts/backup_appdb.sh` creates a backup; after replacing `data/app.db` the state is restored.
- [ ] `.venv/bin/python -m pytest -q` passes (including the golden test against `tests/golden/matches.csv`).
