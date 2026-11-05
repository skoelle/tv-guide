# TV-Guide

EPG aus TVHeadend durchstöbern, Filme und Serien (80er und 90er) finden, gespeicherte Suchen mit E-Mail-Benachrichtigung.
Details: `SPEC.md` (was) und `PLAN.md` (wie). Stand dieses Pakets: Meilensteine M0 bis M2.

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
| `./run.sh initdb` | `app.db` anlegen und migrieren |
| `./run.sh config` | geladene Konfiguration anzeigen |
| `.venv/bin/python -m pytest -q` | Tests |

Mit `--config /pfad/config.toml` (vor oder nach dem Befehl) oder `TVGUIDE_CONFIG` lässt sich eine andere Konfiguration wählen.

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

## Manuelle Abnahme M0 bis M2

- [ ] `./scripts/check_env.sh` zeigt keine `[FAIL]`-Zeile.
- [ ] `./run.sh initdb` meldet Schema-Version 1.
- [ ] `./run.sh status` zeigt 195 Sender, 49.669 Sendungen und 1.285.899 IMDb-Titel.
- [ ] `http://xubuntu-dev:8085/status` zeigt dieselben Zahlen im Browser (auch vom Notebook aus).
- [ ] `./run.sh match` meldet auto 1.425 / review 631 / none 1.556 (Laufzeit unter 2 Minuten).
- [ ] Danach zeigt `./run.sh status` dieselben Zuordnungen und `Shows` in `app.db`.
- [ ] `.venv/bin/python -m pytest -q` läuft grün (inkl. Golden-Test gegen `tests/golden/matches.csv`).
