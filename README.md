# TV-Guide

[![build](https://github.com/skoelle/tv-guide/actions/workflows/build.yml/badge.svg)](https://github.com/skoelle/tv-guide/actions/workflows/build.yml)

EPG aus TVHeadend durchstöbern, Filme und Serien (80er und 90er) finden, gespeicherte Suchen mit E-Mail-Benachrichtigung.
Details: `SPEC.md` (was) und `PLAN.md` (wie). Stand dieses Pakets: Meilensteine M0 bis M8.

## Voraussetzungen

- Python 3.11 oder neuer (getestet mit 3.12.3), `python3-venv`
- `data/epg.db` und `data/imdb.db` (von `tools/epg_import.py` und `tools/imdb_build.py`)
- `tools/` mit den drei Skripten liegt bereits im Repo und bleibt unverändert

## Erste Schritte

```bash
./setup.sh                  # .venv anlegen, Abhängigkeiten installieren, config.toml erzeugen
vim config.toml             # Pfade und Port prüfen
./scripts/check_env.sh      # Umgebung prüfen (M0)
./run.sh initdb             # app.db anlegen
./run.sh status             # Datenstand im Terminal
./run.sh                    # Webserver starten, dann http://xubuntu-dev:8085/status öffnen
```

## Befehle

| Befehl | Wirkung |
|---|---|
| `./run.sh` oder `./run.sh serve` | Webserver (Port aus `config.toml`) |
| `./run.sh status [--json]` | Datenstand von EPG, IMDb und App |
| `./run.sh match` | EPG-Shows gegen IMDb matchen und in `app.db` schreiben |
| `./run.sh refresh [--force]` | geänderte `epg.db` erkennen und nur dann matchen |
| `./run.sh searches` | gespeicherte Suchen auswerten (`search_hits`) |
| `./run.sh notify [--dry-run] [--test]` | Benachrichtigung über neue Treffer versenden |
| `scripts/sync_data.sh epg.db` | `epg.db` atomar von der Synology holen |
| `./run.sh initdb` | `app.db` anlegen und migrieren |
| `./run.sh config` | geladene Konfiguration anzeigen |
| `.venv/bin/python -m pytest -q` | Tests |

Mit `--config /pfad/config.toml` (vor oder nach dem Befehl) oder `TVGUIDE_CONFIG` lässt sich eine andere Konfiguration wählen.

## Web-Ansichten

| Pfad | Inhalt |
|---|---|
| `/` | Jetzt: „Läuft gerade" und „Heute Abend" mit Filterleiste |
| `/browse` | Stöbern: Karten pro Werk, Filter-Chips, Sortierung, Paginierung |
| `/work/{tconst}` | Werk-Detail mit allen Terminen |
| `/review` | Offene Zuordnungen prüfen, bestätigen, ablehnen, anderer Titel, rückgängig |
| `/searches` | Gespeicherte Suchen: Treffer, „neu", bearbeiten, löschen, aktualisieren |
| `/status` | Datenstand |
| `/api/now`, `/api/browse`, `/api/works/{tconst}`, `/api/review`, `/api/searches`, `/api/status` | JSON |

## E-Mail (ab M7)

Nach jedem `refresh` (und über `./run.sh notify`) verschickt der TV-Guide **eine Sammelmail** für alle offenen Treffer von Suchen mit „benachrichtigen". In der `[smtp]`-Sektion in `config.toml` stehen `host`, `port`, `security` (`starttls`|`ssl`), `username`, `from`, `to`; das Passwort kommt ausschließlich aus der Umgebungsvariable `TVGUIDE_SMTP_PASSWORD`. Links in der Mail nutzen `[server].base_url`. `notified_at` wird erst nach erfolgreichem Versand gesetzt – bei fehlendem SMTP oder Fehlern wird beim nächsten Lauf nachgeholt. `./run.sh notify --dry-run` schreibt die Mail nach `data/outbox/`, `--test` sendet eine Testmail.

Gespeicherte Suchen speichern ein **validiertes Filter-JSON** (Whitelist: `type`, `decade`, `group`, `genre`, `retro`, `q`, plus `include_review`/`notify`). Ausgewertet wird mit derselben Engine wie die Filteransicht, deshalb liefern Suche und Ansicht dieselben Treffer. `search_hits` wird bei `refresh` und über `./run.sh searches` fortgeschrieben; „neu" ist die Anzahl der beim letzten Lauf neu hinzugekommenen Termine.

Review-Entscheidungen landen in `overrides` (haben Vorrang vor der Automatik) und werden in `override_log` protokolliert, damit die letzte Entscheidung rückgängig gemacht werden kann.

Filter werden über Query-Parameter gesteuert (`?type=film&decade=1980,1990&genre=Action&group=retro&status=sicher&retro=1&sort=jahr&q=…`). Die Seite arbeitet mit HTMX lokal (`static/htmx.min.js`) und funktioniert auch ohne JavaScript als normales Formular. `?at=<unixtime>` friert den Zeitpunkt für Tests ein.

## Betrieb mit Docker (ab M8)

> **Entwicklung ≠ Betrieb:** Auf `xubuntu-dev` läuft **kein Container**. Dort werden ausschließlich lokale Python-Tests ausgeführt (`./run.sh`, `.venv/bin/python -m pytest -q`). Docker und das GHCR-Image kommen nur auf dem Zielhost (Synology/Docker-Host) zum Einsatz.

Voraussetzungen: Docker + Compose, eine vorhandene `config.toml` und `data/` mit `epg.db`/`imdb.db`.

```bash
cp config.example.toml config.toml      # einmalig, Pfade/Port/SMTP anpassen
docker compose up -d --build            # bauen und starten (Port 8085)
docker compose logs -f tvguide          # Logs
docker compose ps                       # Status inkl. Healthcheck
```

Volumes: `./config.toml` (read-only) nach `/app/config.toml`, `./channels_ignore.txt` (read-only) nach `/app/channels_ignore.txt` und `./data` nach `/app/data`. Die Ignore-Liste muss neben `config.toml` liegen (Pfad wird relativ zur Konfiguration aufgelöst); ohne sie werden Radio-/Shopping-Sender nicht gefiltert. `app.db` (Overrides, Suchen, Treffer) liegt im Volume und überlebt Neustarts und Updates. `TVGUIDE_SMTP_PASSWORD` kommt aus `.env` (nicht im Git); `restart: unless-stopped` startet den Container nach einem Neustart des Hosts automatisch.

### Image von GHCR ziehen (Build in GitHub)

GitHub Actions baut bei jedem Push auf `main` (und wöchentlich) das Image und legt es als `ghcr.io/skoelle/tv-guide:latest` ab ([build-Workflow](https://github.com/skoelle/tv-guide/actions/workflows/build.yml)). Damit musst du lokal nichts bauen:

```bash
cp docker-compose.example.yml docker-compose.yml   # Pfade in den Volumes anpassen
docker compose pull
docker compose up -d
```

Das Image enthält nur den Code; `config.toml`, `channels_ignore.txt` und `data/` werden als Volumes eingebunden. Ist das GHCR-Paket noch privat, in GitHub unter *Packages* auf **public** stellen (oder einmalig `docker login ghcr.io` ausführen).

Für automatisches Nachziehen ist im Beispiel ein Watchtower-Label gesetzt. Setzt du im Repo die Secrets `WEBHOOK_URL` (und optional `WEBHOOK_TOKEN`), ruft der Build nach dem Push deinen Watchtower-Webhook auf.

**Aktualisieren** (Import auf der Synology, dann Daten holen und refreshen):

```bash
TVGUIDE_SYNC_HOST=stefan@synology ./scripts/docker_update.sh epg.db
```

Das holt die Datei atomar nach `data/`, führt im Container `refresh` aus (Match + Suchen + Mail) und lässt `app.db` unangetastet.

**Zeitplan** (Cron auf dem Docker-Host oder Synology Task Scheduler):

```cron
15 6 * * * cd /pfad/tv-guide && ./scripts/docker_update.sh epg.db >> data/update.log 2>&1
```

**Sicherung und Wiederherstellung** (`app.db` ist der einzige nicht reproduzierbare Zustand):

```bash
./scripts/backup_appdb.sh                       # -> backup/app_<stamp>.db (WAL-sicher)
docker compose stop
cp backup/app_<stamp>.db data/app.db            # vorher data/app.db-wal und -shm entfernen
docker compose start
```

`epg.db` und `imdb.db` sind reproduzierbar und müssen nicht gesichert werden. `backup/`, `data/` und `config.toml` stehen in `.gitignore`.

## Datenfluss

```
Synology:  epg_import.py -> epg.db        imdb_build.py -> imdb.db
              |  rsync (atomar kopieren)       |
xubuntu-dev:  data/epg.db (read-only)    data/imdb.db (read-only)
                          \               /
                           tvguide  ->  data/app.db (Zuordnungen, Overrides, Suchen)
```

`epg.db` und `imdb.db` werden nie von der App verändert. Der Nutzerzustand liegt nur in `app.db`.

## Hinweise

- SQLite-Dateien nur auf lokalen Dateisystemen ablegen (kein SMB/NFS).
- `*.db`, `data/`, `config.toml` stehen in `.gitignore`.
- IMDb-Daten nur für persönliche, nicht kommerzielle Nutzung, Quellenangabe steht im Seitenfuß.

## Manuelle Abnahme M0 bis M8

- [ ] `./scripts/check_env.sh` zeigt keine `[FAIL]`-Zeile.
- [ ] `./run.sh initdb` meldet Schema-Version 4.
- [ ] `./run.sh status` zeigt 195 Sender, 49.669 Sendungen und 1.285.899 IMDb-Titel.
- [ ] `http://xubuntu-dev:8085/status` zeigt dieselben Zahlen im Browser (auch vom Notebook aus).
- [ ] `./run.sh match` meldet auto 1.425 / review 631 / none 1.556 (Laufzeit unter 2 Minuten).
- [ ] Danach zeigt `./run.sh status` dieselben Zuordnungen und `Shows` in `app.db`.
- [ ] `./run.sh refresh` meldet beim ersten Mal neue Zuordnungen, beim zweiten Mal „unverändert".
- [ ] `http://xubuntu-dev:8085/` zeigt unter „Stöbern" Das A-Team, MacGyver und die Olsenbande-Filme mit Jahr und Terminen.
- [ ] Filter (Typ, Dekade, Sendergruppe, Genre, Zuordnung, Retro) und Sortierung wirken; „Läuft gerade" stimmt mit der Uhrzeit überein.
- [ ] Seiten laden spürbar unter einer Sekunde (Katalog wird pro Prozess gecacht).
- [ ] Unter „Review" lässt sich ein Vorschlag bestätigen; die Show gilt danach als „sicher" in Jetzt/Stöbern und bleibt nach einem Refresh erhalten.
- [ ] „Letzte Entscheidung rückgängig" stellt den vorherigen Zustand wieder her.
- [ ] Eine gespeicherte Suche „Serien der 80er und 90er" liefert dieselben Treffer wie die Filteransicht; nach „Treffer jetzt aktualisieren" steigt „neu" einmalig.
- [ ] `./run.sh notify --dry-run` schreibt eine `.eml` nach `data/outbox/`; mit gesetztem SMTP löst ein neuer Treffer genau eine Mail aus, ein erneuter Refresh keine weitere.
- [ ] `docker compose up -d --build` startet den Dienst; `/healthz` liefert `{"ok": true}` und `/status` ist erreichbar.
- [ ] Ein Neustart des Containers/Hosts behält Overrides und Suchen (`app.db` im Volume).
- [ ] `./scripts/backup_appdb.sh` erzeugt ein Backup; nach Austausch von `data/app.db` ist der Zustand wiederhergestellt.
- [ ] `.venv/bin/python -m pytest -q` läuft grün (inkl. Golden-Test gegen `tests/golden/matches.csv`).
