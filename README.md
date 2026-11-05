# TV-Guide

EPG aus TVHeadend durchstöbern, Filme und Serien (80er und 90er) finden, gespeicherte Suchen mit E-Mail-Benachrichtigung.
Details: `SPEC.md` (was) und `PLAN.md` (wie). Stand dieses Pakets: Meilensteine M0 bis M6.

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

Gespeicherte Suchen speichern ein **validiertes Filter-JSON** (Whitelist: `type`, `decade`, `group`, `genre`, `retro`, `q`, plus `include_review`/`notify`). Ausgewertet wird mit derselben Engine wie die Filteransicht, deshalb liefern Suche und Ansicht dieselben Treffer. `search_hits` wird bei `refresh` und über `./run.sh searches` fortgeschrieben; „neu" ist die Anzahl der beim letzten Lauf neu hinzugekommenen Termine.

Review-Entscheidungen landen in `overrides` (haben Vorrang vor der Automatik) und werden in `override_log` protokolliert, damit die letzte Entscheidung rückgängig gemacht werden kann.

Filter werden über Query-Parameter gesteuert (`?type=film&decade=1980,1990&genre=Action&group=retro&status=sicher&retro=1&sort=jahr&q=…`). Die Seite arbeitet mit HTMX lokal (`static/htmx.min.js`) und funktioniert auch ohne JavaScript als normales Formular. `?at=<unixtime>` friert den Zeitpunkt für Tests ein.

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

## Manuelle Abnahme M0 bis M6

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
- [ ] `.venv/bin/python -m pytest -q` läuft grün (inkl. Golden-Test gegen `tests/golden/matches.csv`).
