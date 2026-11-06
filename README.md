# 📺 TV-Guide

[![build](https://github.com/skoelle/tv-guide/actions/workflows/build.yml/badge.svg)](https://github.com/skoelle/tv-guide/actions/workflows/build.yml)

Browse the TVHeadend EPG, discover movies and series from the 80s and 90s 🍿📼, and keep saved searches with email notifications 📧.
Details: `SPEC.md` (what) and `PLAN.md` (how).

## ✨ Features

- 🔍 **Browse** the TVHeadend EPG with "running now", "tonight", and powerful filters
- 🎯 **Match** shows against IMDb data, with a review queue for uncertain matches
- 🎬 **Film, Serie, Format** — fiction stays visible, non-fiction formats are hidden by default
- 🖼️ **TMDB posters**, cached locally and shown in Now, Browse, and work details
- 🔎 **Saved searches** with validated filters and hit tracking
- 📧 **Email digest** for new hits, retried automatically on failure
- ⏰ **Automatic sync** for EPG (hourly) and IMDb (weekly)
- 🐳 **Docker-ready** with GHCR image, scheduler, backup, and restore scripts

## 📦 Requirements

- 🐍 Python 3.11 or newer (tested with 3.12.3), `python3-venv`
- 🗄️ `data/epg.db` and `data/imdb.db` (built by `tools/epg_import.py` and `tools/imdb_build.py`)
- 🧰 `tools/` with the three scripts is already in the repo and stays unchanged

## 🚀 Quick start

```bash
./setup.sh                  # create .venv, install dependencies, create config.toml
vim config.toml             # check paths and port
./scripts/check_env.sh      # verify the environment
./run.sh initdb             # create app.db
./run.sh status             # show data status in the terminal
./run.sh                    # start the web server, then open http://xubuntu-dev:8085/status
```

## 🛠️ Commands

| Command | Effect |
|---|---|
| `./run.sh` or `./run.sh serve` | Web server (port from `config.toml`) |
| `./run.sh status [--json]` | Status of EPG, IMDb and app data |
| `./run.sh match` | Match EPG shows against IMDb and write to `app.db` |
| `./run.sh refresh [--force]` | Detect changed EPG/IMDb data and only then re-match |
| `./run.sh epg` | Import TVHeadend XMLTV (atomic, change-aware) and refresh |
| `./run.sh imdb` | Build the IMDb database (weekly) and refresh |
| `./run.sh posters [--limit N] [--force] [--prune]` | Fetch missing TMDB posters and cache them locally |
| `./run.sh searches` | Evaluate saved searches (`search_hits`) |
| `./run.sh notify [--dry-run] [--test]` | Send notifications for new hits |
| `scripts/sync_data.sh epg.db` | Fetch `epg.db` from the Synology atomically |
| `./run.sh initdb` | Create and migrate `app.db` |
| `./run.sh config` | Show the loaded configuration |
| `.venv/bin/python -m pytest -q` | Tests |
| `ruff check .` | Lint (configuration in `ruff.toml`) |

Use `--config /path/config.toml` (before or after the command) or `TVGUIDE_CONFIG` to select a different configuration.

## 🖥️ Web views

| Path | Content |
|---|---|
| `/` | Now: "running now" and "tonight" with a filter bar |
| `/browse` | Browse: cards per work, filter chips, sorting, pagination |
| `/work/{tconst}` | Work detail with IMDb title, TMDB link, all airings, and review actions |
| `/review` | Review open matches: confirm, reject, pick another title, undo |
| `/searches` | Saved searches: hits, "new", edit, delete, refresh |
| `/status` | Data status |
| `/api/now`, `/api/browse`, `/api/works/{tconst}`, `/api/review`, `/api/searches`, `/api/jobs`, `/api/status` | JSON |
| `/poster/{tconst}` | Locally cached TMDB poster |
| `POST /api/review/confirm`, `POST /api/review/reject`, `POST /api/review/undo` | Review actions for HTMX (JSON, no redirect) |
| `POST /api/works/{tconst}/reopen` | Remove a match for all shows of a work and put them back into Review |
| `/partials/jobs` | HTMX fragment with the sync job status |

## 🎬 Content categories

Every work is classified as **Film**, **Serie** or **Format**:

- 🎬 **Film** – IMDb type `movie` / `tvMovie`
- 📋 **Format** – series with genres News, Talk-Show, Game-Show, Reality-TV, Documentary, Music, Sport or Short (magazines, news, quiz shows, documentaries)
- 📺 **Serie** – everything else

Browse and Now show **Film + Serie** by default; the **Format** chip reveals the rest. Review only lists Film and Serie, so magazines, game shows and news formats no longer appear there.

Manual control: a work classified as **Format** can be kept as a **film/series** (this pulls an automatically detected format back); other works can be marked as **Format**. The last manual mark can be undone on the Review page. Marks are stored per IMDb title in `content_marks` and take precedence over the automatic genre classification.

Uncertain matches can also be resolved directly on the work page: confirm the current match, reject it, or pick another title. The same candidates and actions remain available on the Review page.

The work detail shows both the EPG title and the IMDb title plus the IMDb ID and, when available, a link to the work's TMDB page. If an automatic match is wrong, **Zuordnung entfernen** on the work detail removes the match for all shows of that work and puts them back into Review with fresh suggestions. Poster and TMDB link disappear with the work; cached poster data is reused if the same IMDb title is assigned again. Orphaned poster files are removed by the next poster job with pruning enabled.

## 🖼️ Posters (TMDB)

Posters are fetched from [The Movie Database (TMDB)](https://www.themoviedb.org/) and cached locally; the page never hotlinks image URLs. The full TMDB JSON responses are stored in `app.db` (`tmdb_cache`), so later features can reuse titles, years, genres, backdrops, and IDs without asking TMDB again. Images live in `data/posters/`. TMDB data is refreshed after at most six months.

Credentials come from the environment, never from `config.toml`:

```bash
TVGUIDE_TMDB_READ_TOKEN=...   # v4 Read Access Token (preferred, sent as Bearer)
TVGUIDE_TMDB_API_KEY=...      # v3 API key fallback
```

The first run resolves every matched work and can take a few minutes (TMDB allows 40 requests per 10 seconds; the default delay is 0.3 s):

```bash
set -a; . ./.env; set +a
./run.sh posters
./run.sh posters --limit 50    # partial run
./run.sh posters --force       # refresh all cached posters
./run.sh posters --prune       # remove images for works that are no longer matched
```

The `/status` page has the same action as **"Fehlende Poster holen"**. The command only needs to run when new works appear or the cache is older than `[posters].refresh_days`; missing titles are retried after `missing_retry_days`, errors after `error_retry_days`.

TMDB attribution: *This product uses the TMDB API but is not endorsed or certified by TMDB.* TMDB data is used for personal, non-commercial purposes only.

## 📧 Email notifications

After every `refresh` (and via `./run.sh notify`) TV-Guide sends **one digest email** for all open hits of searches with notifications enabled. The `[smtp]` section in `config.toml` holds `host`, `port`, `security` (`starttls`|`ssl`), `username`, `from`, `to`; the password comes exclusively from the `TVGUIDE_SMTP_PASSWORD` environment variable. Links in the email use `[server].base_url`. `notified_at` is only set after a successful send – if SMTP is missing or sending fails, the next run retries. `./run.sh notify --dry-run` writes the email to `data/outbox/`, `--test` sends a test message.

Saved searches store a **validated filter JSON** (whitelist: `type`, `decade`, `group`, `genre`, `retro`, `q`, plus `include_review`/`notify`). They are evaluated with the same engine as the filter view, so a search and the view always return the same hits. `search_hits` is updated on `refresh` and via `./run.sh searches`; "new" is the number of airings added during the last run.

Review decisions are stored in `overrides` (they take precedence over the automatic matching) and logged in `override_log` so the last decision can be undone.

Filters are controlled via query parameters (`?type=film&decade=1980,1990&genre=Action&group=retro&status=sicher&retro=1&sort=jahr&q=...`). The page uses a local HTMX build (`static/htmx.min.js`) and still works without JavaScript as a plain form. `?at=<unixtime>` freezes the point in time for tests.

## ⏰ Automatic sync (scheduler + buttons)

The container can keep EPG and IMDb up to date by itself:

- **EPG** is checked every `[schedule].epg_every_minutes` (default hourly). The import is cheap and only triggers a re-match when the XMLTV content (SHA1) changed.
- **IMDb** is rebuilt weekly (`[schedule].imdb_weekday` / `imdb_time`, default Monday 04:30 Europe/Berlin). A re-match only happens if the dataset date or the build parameters changed.
- The `/status` page has the buttons **"EPG jetzt importieren"**, **"IMDb jetzt neu bauen"** and **"Fehlende Poster holen"**. They call `POST /api/jobs/{kind}` (JSON, no redirect) via HTMX. Only one job runs at a time; the page polls the job status and shows the last log lines.
- The scheduler lives inside the web container (single process, background thread). State (`last_epg_run`, `last_imdb_run`) is kept in `app_meta`, so it survives restarts. Jobs interrupted by a restart are marked as `error` and retried on the next schedule.

Relevant config/env:

```toml
[epg]
url = "http://docker-host-nas.fritz.box:9981/xmltv/channels"
user = ""                 # password from TVGUIDE_TVH_PASSWORD

[imdb]
raw_dir = "data/imdb_raw" # large dataset, lives in the data volume

[schedule]
enabled = false           # docker-compose sets TVGUIDE_SCHEDULE_ENABLED=true
epg_every_minutes = 60
imdb_weekday = 0          # Monday
imdb_time = "04:30"
```

The IMDb build needs network access to `datasets.imdbws.com` and about 2 GB of free disk (`imdb_raw` + temporary build + final database); the job aborts early with a clear message if space is insufficient. Jobs and logs are stored in `data/logs/`.

## 🐳 Docker operation

> **Development ≠ operation:** `xubuntu-dev` runs **no container**. It is used for local Python tests only (`./run.sh`, `.venv/bin/python -m pytest -q`). Docker and the GHCR image are only used on the target host (Synology/Docker host).

Requirements: Docker + Compose, an existing `config.toml`, and `data/` with `epg.db`/`imdb.db`.

```bash
cp config.example.toml config.toml      # once; adjust paths/port/SMTP
docker compose up -d --build            # build and start (port 8085)
docker compose logs -f tvguide          # logs
docker compose ps                       # status including healthcheck
```

Volumes: `./config.toml` (read-only) to `/app/config.toml`, `./channels_ignore.txt` (read-only) to `/app/channels_ignore.txt`, and `./data` to `/app/data`. The ignore list must live next to `config.toml` (the path is resolved relative to the configuration file); without it, radio and shopping channels are not filtered out. Changes to the ignore list are detected by content on the next refresh, even when the EPG is unchanged; to apply them immediately run `docker compose exec tvguide python -m tvguide refresh --force` (or `docker exec -it tv-guide python -m tvguide refresh --force`). `app.db` (overrides, searches, hits, poster cache) lives in the volume and survives restarts and updates. `TVGUIDE_SMTP_PASSWORD`, `TVGUIDE_TVH_PASSWORD`, `TVGUIDE_TMDB_READ_TOKEN` and `TVGUIDE_TMDB_API_KEY` come from `.env` (copy from `.env.example`, not committed); the bundled compose files set `TVGUIDE_SCHEDULE_ENABLED=true`. After the first deployment, fetch the posters once:

```bash
docker compose exec tvguide python -m tvguide posters
``` `restart: unless-stopped` restarts the container automatically after a host reboot.

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

## 🔄 Data flow

```
Synology:   epg_import.py -> epg.db        imdb_build.py -> imdb.db
               |  rsync (atomic copy)          |
xubuntu-dev:  data/epg.db (read-only)    data/imdb.db (read-only)
                          \               /
                           tvguide  ->  data/app.db (matches, overrides, searches)
```

`epg.db` and `imdb.db` are never modified by the app. User state, the TMDB JSON cache, and poster metadata live in `app.db`; poster images are stored in `data/posters/`.

## 📝 Notes

- 💾 Keep SQLite files on local filesystems only (no SMB/NFS).
- 🙈 `.gitignore` excludes `data/`, `backup/`, `*.db`, `config.toml`, `channels_ignore.txt`, `.env`, `tmp/` and Synology `@eaDir/`.
- 🔐 `cp .env.example .env` and fill in the passwords for Docker deployments; `.env` is git-ignored.
- ⚖️ IMDb data is for personal, non-commercial use only; attribution is shown in the page footer.
- 🖼️ TMDB posters are cached for at most six months; attribution is shown in the page footer.
- 🎨 The retro TV favicon lives in `tvguide/web/static/`; regenerate the raster files (PNG/ICO) with `python3 scripts/make_favicon.py` (dev-only, needs Pillow).
