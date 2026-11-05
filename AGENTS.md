# AGENTS.md

Guidance for AI agents and contributors working in this repository.

## Project

Private TV guide: reads a TVHeadend EPG, matches shows against IMDb data, and
offers a FastAPI web UI with saved searches and email notifications.

Layout:

- `tvguide/` – application (config, db, matching, browse, review, search, notify, web)
- `tools/` – standalone data tools (`epg_import.py`, `imdb_build.py`, `match_test.py`); keep unchanged
- `tests/` – pytest suite (golden test skips when `data/` is missing)
- `data/` – local databases (`epg.db`, `imdb.db`, `app.db`); never committed
- `SPEC.md`, `PLAN.md` – specification and plan (German)
- `README.md`, `AGENTS.md` – English

## Setup and commands

```bash
./setup.sh                      # create .venv, install deps, create config.toml
./run.sh initdb                 # create/migrate app.db
./run.sh status                 # data status
./run.sh match                  # match EPG shows against IMDb
./run.sh refresh                # detect changed epg.db, then match + searches + email
./run.sh searches               # update search_hits
./run.sh notify --dry-run       # write an .eml to data/outbox/
./run.sh epg                    # import TVHeadend XMLTV and refresh
./run.sh imdb                   # build IMDb datasets and refresh
./run.sh                        # web server on port 8085
.venv/bin/python -m pytest -q   # tests
ruff check .                    # lint (config in ruff.toml)
```

`refresh` detects changes by content (EPG XML SHA1, IMDb source_date + build params),
not by file mtime. In the container a background scheduler runs EPG hourly and IMDb
weekly (`TVGUIDE_SCHEDULE_ENABLED=true`); the same work can be triggered from the
`/status` buttons. Jobs run one at a time and log to `data/logs/`.

## Conventions

- **README, AGENTS.md and git commit messages are written in English** from now on.
  Existing code comments/docstrings may stay German.
- Never add co-authors to commits.
- Never commit `data/`, `config.toml`, `channels_ignore.txt`, `backup/`, `.env`, or databases.
- Commit per milestone; keep messages concise and imperative.
- After `git push`, do **not** wait for or poll the GitHub Actions run. Report the commit
  and let CI run in the background; check with `gh run list` only when explicitly asked.
- Do not add Python dependencies beyond what `requirements.txt` needs without good reason;
  the Docker image installs only `requirements.txt` (`requirements-dev.txt` adds ruff).

## Environment

- `xubuntu-dev` is for local Python development and tests only — **never run Docker/containers there**.
- Docker and the GHCR image are used on the target host (Synology/Docker host) only.
- CI (GitHub Actions) runs `ruff` + `pytest`, builds `ghcr.io/skoelle/tv-guide`, and calls a
  Watchtower webhook (`WEBHOOK_URL`/`WEBHOOK_TOKEN`) to roll out the new image.

## Tests

- The golden test depends on the real `data/*.db` and `channels_ignore.txt` and is skipped when they are absent.
- Match quality is validated against `tests/golden/matches.csv` from a real `tools/match_test.py` run.
