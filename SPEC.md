# SPEC: TV-Guide (Arbeitstitel `tv-guide`)

Status: v1.0 „as built“, 2026-11-05. Beschreibt den tatsächlich umgesetzten Stand
(M0–M10). Abweichungen von früheren Entwürfen sind unten direkt eingearbeitet.

## 1. Ziel

Ein privater Dienst im Heimnetz, der das TVHeadend-EPG einliest, jede Sendung einem
bekannten Film oder einer Serie zuordnet (inklusive Produktionsjahr, Laufzeit der Serie,
Genre, Bewertung) und das Programm durchstöberbar macht. Schwerpunkt: alte Spielfilme
und alte Serien (80er und 90er), die gerade oder demnächst laufen. Dazu gespeicherte
Suchen mit E-Mail-Benachrichtigung.

### 1.1 Nicht-Ziele

- Keine Aufnahmesteuerung (kein DVR-Zugriff, keine Timer).
- Kein öffentlicher Zugriff, keine Mehrbenutzerverwaltung. Ein Login gibt es nicht;
  im Heimnetz steht optional ein Reverse Proxy mit Authentifizierung (z. B. Authelia) davor.
- Kein Scraping von IMDb oder anderen Webseiten.
- Keine kommerzielle Nutzung (IMDb-Datasets sind nur für persönliche, nicht kommerzielle Nutzung).
- `xubuntu-dev` ist reine Entwicklungs-/Testumgebung mit lokalem Python; dort läuft kein Container.

### 1.2 Leitprinzipien

- Die App liest `epg.db` und `imdb.db` nur lesend und hält Nutzerzustand ausschließlich in `app.db`.
- EPG-Import und IMDb-Build sind eigenständige Werkzeuge (nur Standardbibliothek). Sie können
  auf der Synology laufen (per rsync angebunden) **oder** im App-Container (Scheduler/Buttons,
  Migration M9). Die Werkzeuge selbst bleiben unverändert.
- Lieber eine unsichere Zuordnung zur Prüfung vorlegen als stillschweigend falsch zuordnen.
- Nicht-fiktionale Formate (Doku, Magazin, News/Talk, Quiz/Reality, Musik/Sport) werden als
  eigene Kategorie „Format“ geführt und nicht als Serie oder im Review angezeigt.
- Wenige, gut gepflegte Pakete; ein schlankes Produktionsimage und ein deterministischer CI-Lauf.

## 2. Architektur

```
Zielhost (Synology oder Docker-Host "Debian")
  Container tv-guide
    - FastAPI/uvicorn (Port 8085)
    - In-App-Scheduler (Daemon-Thread)
    - Jobs: tools/epg_import.py, tools/imdb_build.py (Subprozess)
    - liest/schreibt: data/app.db, data/epg.db, data/imdb.db, data/logs/, data/outbox/
  Volumes: config.toml (ro), channels_ignore.txt (ro), data/ (rw, lokale Disk)

Optional weiterhin: Import auf der Synology -> epg.db/imdb.db -> rsync -> data/
```

Drei SQLite-Dateien mit klarer Trennung:

| Datei | Geschrieben von | Inhalt | Austauschbar? |
|---|---|---|---|
| `epg.db` | `tools/epg_import.py` | Sender und Sendungen (Rohdaten) | Ja, wird fortgeschrieben |
| `imdb.db` | `tools/imdb_build.py` | Gefilterte IMDb-Titel und Namen | Ja, wird komplett neu gebaut |
| `app.db` | App | Zuordnungen, Kategorien, Vorschläge, Suchen, Treffer, Jobs | Nein, enthält Nutzerzustand |

Hinweise zu SQLite:

- Alle Dateien auf einem lokalen Dateisystem halten (kein SMB/NFS). WAL ist in `app.db` aktiv.
- `epg.db`/`imdb.db` werden read-only per `ATTACH` eingebunden (`mode=ro`, Fallback `immutable=1`).
- Ein rsync-Austausch von außen erfolgt atomar in eine Temp-Datei und `mv`. Im Container
  importiert `epg_import.py` direkt in `epg.db` (SQLite-WAL erlaubt Leser während des Imports;
  committet wird am Ende); `imdb_build.py` schreibt nach `.building` und ersetzt atomar.

## 3. Werkzeuge (außerhalb der App, unverändert)

Alle Werkzeuge sind eigenständige Skripte und benötigen nur Python 3.9+.

### 3.1 `tools/epg_import.py`
- Lädt XMLTV von TVHeadend (URL/Auth konfigurierbar), parst tolerant (UTF-8-Fehler,
  Steuerzeichen, DOCTYPE) und schreibt `epg.db`.
- Upsert nach `(channel_id, start_ts)`, Änderungserkennung über `content_hash`,
  Protokollierung in `imports` (inkl. `xml_sha1`).
- Optionen: `--file`, `--db`, `--url`, `--user`, `--password`, `--keep-xml`, `--prune-days`.

### 3.2 `tools/imdb_build.py`
- Lädt `title.basics`, `title.akas`, `title.ratings` von `datasets.imdbws.com` und baut `imdb.db`.
- Behält `movie`, `tvMovie`, `tvSeries`, `tvMiniSeries`; optional `--with-episodes`.
- Speichert normalisierte Namen (`norm`) und Metadaten (`source_date`, `types`, `min_votes`,
  `min_year`, `with_episodes`, Attribution).
- Optionen: `--min-votes`, `--min-year`, `--types`, `--adult`, `--no-download`, `--force`,
  `--raw-dir`, `--db`, `--with-episodes`.

### 3.3 `tools/match_test.py` (v2.1)
Matching-Prototyp und Regressionswerkzeug. Erzeugt `matches.csv`, `report.txt`,
`channels_report.csv`, `channels_ignore.suggested.txt`. Die Regeln sind in die App
übernommen; `tests/golden/matches.csv` stammt aus einem echten Lauf.

### 3.4 Normalisierung
`tvguide/normalize.py` ist der Vertrag zwischen IMDb-Namen und EPG-Titeln (identisch zu
den Kopien in `tools/`). Änderungen erfordern einen `imdb.db`-Neubau. Ein
`meta.normalize_version` ist noch nicht implementiert (offener Punkt).

## 4. Datenmodell `app.db`

Migrationen liegen in `tvguide/db.py`; `schema_version` steht aktuell auf **6**.

Aus SPEC-Entwurf (Migration 1):
- `epg_shows(id, show_key UNIQUE, show_name, first_seen)`
- `show_match(show_id PK, tconst, status, score, method, reason, updated_at)` –
  `status` ∈ `auto|review|confirmed|rejected|none`; `reason` enthält die Score-Signale als JSON.
- `event_show(channel_id, start_ts, show_id, episode_hint, PK(channel_id,start_ts))`
- `overrides(show_key PK, tconst, note, created_at)` – Vorrang vor der Automatik.
- `saved_searches(id, name, filter_json, include_review, notify, created_at, last_run_at)`
- `search_hits(search_id, channel_id, start_ts, found_at, notified_at, PK(...))`

Spätere Migrationen (as built):
- Migration 2: `app_meta(key, value)` – Refresh-Zustand (`epg_sha1`, `imdb_key`,
  `last_epg_run`, `last_imdb_run`, `last_refresh`).
- Migration 3: `override_log(...)` – Verlauf der Review-Entscheidungen für „rückgängig“.
- Migration 4: `saved_searches.last_new` – Treffer des letzten Suchlaufs.
- Migration 5: `jobs(id, kind, status, started_at, finished_at, message, log_path)` –
  Hintergrundjobs (EPG/IMDb/Refresh), genau einer gleichzeitig.
- Migration 6: `content_marks(id, tconst UNIQUE, mark, created_at)` –
  manuelle Inhaltskategorie (`format` = als Format markieren, `keep` = als Film/Serie behalten).

Die App liest Sendungen aus `epg.db`, IMDb-Daten aus `imdb.db`; Nutzerzustand nur in `app.db`.

## 5. Titelextraktion, Matching und Kategorien

### 5.1 Beobachtungen aus dem echten EPG (Stand 2026-11)
- 49.669 Sendungen, 195 Sender, Zeitraum 01.11. bis 29.11.2026.
- Felder: `sub-title` 66 %, `desc` 53 %, `category` 23 %, `rating` (FSK) 6 %.
- Kein Produktionsjahr, keine Credits, keine Episodennummern. Rund 40 % der Sendungen
  stammen von Radiosendern (`channels_ignore.txt`).
- Folgenmarker im Titel: `(S03/E12)`, `(94)`, `(Folge 1 von 10)`.

### 5.2 Pipeline
1. **Senderfilter** über `channels_ignore.txt` (Namen oder `re:`).
2. **Bereinigen** von Folgenmarkern, `(HD)`, `Teil N`, `Staffel N`, `Wiederholung`;
   Jahr in Klammern bleibt als Hinweis.
3. **Gruppieren** nach bereinigtem Titel (`show_key`).
4. **Vorfilter (Skip)** aus `[skip]`: Kategorien, Titel, `skip_short`, `skip_block`.
5. **Kandidaten** (voller Titel, Präfixe, Suffix) und exakter `title_names.norm`-Lookup.
6. **Bewerten** (Summe gewichteter Signale, Details siehe `tvguide/matching.py`):
   Name, Typ/Laufzeit, Serienerkennung, Laufzeitverhältnis, Stimmen, Jahr, Ambiguität.
7. **Entscheiden:** `auto` ab `[matching].auto_threshold`, `review` ab `review_threshold`,
   sonst `none`. Anzeige-Konfidenz `min(1, score/1.3)`.
8. **Overrides** gelten immer zuerst.
9. **Kategorie** (siehe 5.3) wird aus dem besten Kandidaten abgeleitet (bzw. durch
   `content_marks` überschrieben).

### 5.3 Inhaltskategorien (M10)
`tvguide/categories.py`:

| Kategorie | Bedingung |
|---|---|
| `film` | IMDb-Typ `movie`/`tvMovie` |
| `format` | Serie mit Genre News, Talk-Show, Game-Show, Reality-TV, Documentary, Music, Sport oder Short |
| `serie` | alle übrigen Serien |

- `content_marks` überschreibt: `format` erzwingt Format, `keep` holt ein automatisch
  erkanntes Format als Film/Serie zurück.
- Der Match-Status bleibt unverändert; die Kategorie ist eine Sicht-/Filter-Dimension.
- Fiktionale Mittagsformate (z. B. Telenovelas) bleiben `serie`.

### 5.4 Retro-Klassen
Aus `start_year`, `end_year`, Typ und Genre (ohne News, Talk-Show, Game-Show, Reality-TV,
Documentary, Music, Sport, Short) und dem Fenster `retro_from`–`retro_to`:

| Klasse | Bedingung |
|---|---|
| `film` | `movie`/`tvMovie`, Startjahr im Fenster |
| `serie_abgeschlossen` | Serie, Startjahr im Fenster, `end_year` ≤ `retro_end_max` |
| `serie_lief_laenger` | Serie, Startjahr im Fenster, `end_year` > `retro_end_max` |
| `serie_laufend` | Serie, Startjahr im Fenster, kein `end_year` |

### 5.5 Cache und Wiederholung
- Matching arbeitet pro Show (`epg_shows`), bestätigte/abgelehnte Treffer werden nicht neu berechnet.
- `refresh` erkennt Änderungen inhaltlich: EPG über `imports.xml_sha1`, IMDb über
  `meta.source_date` + Build-Parameter (`types`, `min_votes`, `min_year`, `with_episodes`).
  Ein bloßer Rebuild mit gleichem Datenstand löst kein Re-Matching aus.
- `load_shows` und der Werk-Katalog sind pro Prozess gecacht; die Signatur umfasst
  `content_marks`, damit manuelle Kategorien sofort wirken.

### 5.6 Ergebnisse des Prototyps (Referenz)
| Kennzahl | Wert |
|---|---|
| Betrachtete Sendungen | 28.676 (von 49.669) |
| Eindeutige Titel | 4.298 |
| auto / review / none | 1.425 / 631 / 1.556 Titel |

Nach Kategorien (Show-Ebene, Stand 2026-11): Review 631 = Doku 165, Magazin 54, Show 62,
Serie 230, Film 120. Review zeigt seit M10 nur Film + Serie (in den Echtdaten 350 statt 631).

## 6. Filter und gespeicherte Suchen

### 6.1 Filterfelder (implementiert)
`type` (film/serie), `category` (film/serie/format), `decade`, `group` (aus
`channel_groups`), `genre`, `status` (sicher/unsicher), `retro`, `q` (Textsuche über Titel
und Untertitel). Standard: Kategorie Film+Serie.

### 6.2 Filter-JSON und Sicherheit
Gespeicherte Suchen speichern ein **validiertes Filter-JSON** (Whitelist in
`tvguide/search.py`; keine Auswertung von Nutzer-Code):

```json
{"type": ["serie"], "category": ["film", "serie"], "decade": [1980, 1999],
 "group": "retro", "genre": ["Crime"], "retro": false, "q": ""}
```

Zusätzlich pro Suche: `include_review` (auch unsichere Treffer) und `notify`.
Abweichung vom früheren Entwurf „SQL-Builder“: Ausgewertet wird mit derselben Engine
wie die Filteransicht (`tvguide/browse.py`), damit Suche und Ansicht garantiert dieselben
Treffer liefern. Fehlt `category`, gilt der Standard Film+Serie.

`search_hits` wird bei `refresh` und per `./run.sh searches` fortgeschrieben. „neu“ ist
die Anzahl der beim letzten Lauf neu hinzugekommenen Termine (`saved_searches.last_new`).

## 7. Oberfläche und API

Technik: FastAPI, Jinja2, HTMX (lokal), SQLite. Kein Frontend-Build, mobil nutzbar.

### 7.1 Ansichten
- **Jetzt:** „Läuft gerade“ und „Heute Abend“, Filterleiste inkl. Kategorie.
- **Stöbern:** Karten pro Werk, Filter-Chips (Kategorie/Typ, Dekade, Sendergruppe, Genre,
  Zuordnung, Retro), Sortierung, Paginierung; Format standardmäßig ausgeblendet.
- **Werk-Detail:** Titel, Kategorie, Jahr, Genres, Rating, IMDb-Link, alle Termine;
  bei unsicheren Zuordnungen eine Review-Box (Kandidaten bestätigen, „Kein Treffer“,
  anderer Titel). Kategorie-Buttons kontextabhängig: „Als Format markieren“ bei
  Film/Serie, „Als Film/Serie behalten“ bei Format, „Markierung entfernen“ nur bei
  manueller Markierung.
- **Review:** nur Film/Serie; Vorschläge bestätigen, „Kein Treffer“, anderer Titel,
  „Als Format markieren“, Override-Undo und Markierungs-Undo.
- **Suchen:** Liste, Treffer/„neu“, bearbeiten, löschen, aktualisieren.
- **Status:** Datenstand, Synchronisierung (Buttons „EPG jetzt importieren“, „IMDb jetzt
  neu bauen“), Job-Status/Log, Zeitplan-Info.

Alle interaktiven Elemente (Jobs, Kategorien, Review-Aktionen) nutzen root-absolute URLs
(`/api/...`, `/review/...`). Die App wird am Root-Pfad betrieben; ein Sub-Pfad unter einem
Reverse Proxy wird nicht unterstützt.

### 7.2 API (Auszug)
| Methode/Pfad | Zweck |
|---|---|
| `GET /api/now` | laufende/heutige Sendungen |
| `GET /api/browse` | gefilterte Werke (inkl. `category`) |
| `GET /api/works/{tconst}` | Werk inkl. Kategorie und Terminen |
| `POST /api/works/{tconst}/mark?mark=format\|keep\|none` | manuelle Kategorie |
| `GET /api/review`, `POST /api/review/{show_id\|show_key}` | Review |
| `POST /api/review/confirm`, `POST /api/review/reject`, `POST /api/review/undo` | Review-Aktionen für HTMX (JSON, kein Redirect) |
| `POST /api/review/format?show_key=…` | Show als Format markieren |
| `POST /api/marks/undo` | letzte Markierung rückgängig |
| `GET/POST /api/searches`, `PUT/DELETE /api/searches/{id}` | gespeicherte Suchen |
| `GET /api/searches/{id}/hits` | Treffer |
| `GET /api/jobs`, `POST /api/jobs/{kind=epg\|imdb}` | Jobs |
| `GET /api/status` | Datenstand |

## 8. Benachrichtigungen per E-Mail

- Nach jedem `refresh` (und via `./run.sh notify`) läuft jede Suche mit `notify=1`;
  offene Treffer (`notified_at IS NULL`, Status auto/confirmed) ergeben **eine Sammelmail**.
- Kanäle: `Notifier`-Schnittstelle, erste Umsetzung `EmailNotifier` (`smtplib`).
- Format: `multipart/alternative` (Text + HTML), Betreff mit Trefferzahl, Links über
  `[server].base_url`.
- Konfiguration `[smtp]`: `host`, `port`, `security` (`starttls`|`ssl`), `username`,
  `from`, `to`. Passwort nur aus `TVGUIDE_SMTP_PASSWORD`.
- `notified_at` erst nach erfolgreichem Versand; Fehler brechen den Refresh nicht ab.
- `./run.sh notify --dry-run` schreibt `.eml` nach `data/outbox/`, `--test` sendet eine Testmail.
- Ein Healthchecks-Ping ist optional und noch nicht umgesetzt.

## 9. Betrieb

### 9.1 Repository-Struktur
```
tv-guide/
  README.md  AGENTS.md  SPEC.md  PLAN.md
  config.example.toml  config.toml (nicht im Git)
  Dockerfile  .dockerignore  docker-compose.yml  docker-compose.example.yml
  .env.example
  .github/workflows/{test,build}.yml
  run.sh  setup.sh  requirements.txt  requirements-dev.txt  ruff.toml
  scripts/ (check_env, sync_data, docker_update, backup_appdb, make_favicon)
  tools/ (epg_import, imdb_build, match_test – unverändert)
  tvguide/
    config.py db.py normalize.py cleaning.py matching.py categories.py marks.py
    browse.py review.py search.py refresh.py pipeline.py jobs.py scheduler.py status.py
    notify/ (__init__, email, digest)
    web/ (app.py, templates/, static/ inkl. htmx und Favicon)
  tests/ (inkl. golden/)
  data/ (nicht im Git)
```

### 9.2 Entwicklung und Deployment
- Entwicklung/Test: `xubuntu-dev`, lokales Python (`./run.sh`, `pytest`, `ruff`); **kein Docker**.
- Produktiv: ein Container (`python:3.12-slim`) auf Synology/Docker-Host, Volume auf lokaler Disk.
- CI (GitHub Actions): `test.yml` (ruff + pytest), `build.yml` (Image nach
  `ghcr.io/skoelle/tv-guide`, wöchentlich/manuell, Watchtower-Webhook `WEBHOOK_URL`/`WEBHOOK_TOKEN`).
- `docker-compose.example.yml` zieht das GHCR-Image; `docker-compose.yml` baut lokal.

### 9.3 Automatischer Abgleich (M9)
- Tools sind im Image; `./run.sh epg` und `./run.sh imdb` rufen sie per Subprozess auf und
  refreshen danach.
- In-App-Scheduler (Daemon-Thread): EPG stündlich, IMDb wöchentlich (Default Montag 04:30
  Europe/Berlin), aktiv über `TVGUIDE_SCHEDULE_ENABLED=true`.
- Jobs: genau einer gleichzeitig, Logs in `data/logs/`, verwaiste Jobs werden nach Neustart
  als `error` markiert. IMDb-Build prüft vorher ~2 GB freien Platz.
- Buttons in `/status` starten dieselben Jobs über `POST /api/jobs/{kind}` (JSON, kein Redirect).

### 9.4 Umgebungsvariablen
- `TVGUIDE_CONFIG` – Pfad zur `config.toml`.
- `TVGUIDE_SMTP_PASSWORD`, `TVGUIDE_TVH_PASSWORD` – Passwörter (nie im Git).
- `TVGUIDE_SCHEDULE_ENABLED` – Scheduler im Container aktivieren.
- Repo-Secrets für CI: `WEBHOOK_URL`, `WEBHOOK_TOKEN`.

### 9.5 Sicherung
- `./scripts/backup_appdb.sh` sichert `app.db` per SQLite-Backup-API (WAL-sicher).
- `epg.db`/`imdb.db` sind reproduzierbar; `app.db` ist der einzige zu sichernde Zustand.

### 9.6 Tests
- Unit: Normalisierung, Titelbereinigung, Matching/Bewertung, Kategorien, Filter, Jobs, Scheduler.
- Golden: `tests/golden/matches.csv` aus einem echten `match_test.py`-Lauf; wird ohne
  `data/` übersprungen.
- Web/API via `fastapi.testclient`; CI läuft ohne echte Daten (`ruff` + `pytest`).

## 10. Lizenz und Datenschutz

- IMDb-Daten nur persönliche, nicht kommerzielle Nutzung; Attribution in `imdb.meta` und im Seitenfuß.
- Keine Weitergabe von `imdb.db`; kein Zugriff von außerhalb des Heimnetzes.
- SMTP-/TVH-Zugangsdaten nur per Umgebungsvariable, nie im Git.
- TMDB/Poster weiterhin nicht verwendet.

## 11. Phasenplan (umgesetzt)

| Phase | Inhalt | Status |
|---|---|---|
| 0 | Werkzeuge `epg_import.py`, `imdb_build.py`, `match_test.py` | erledigt |
| M0 | Umgebung, Repo, `.gitignore`, venv, FTS5 | erledigt |
| M1 | Grundgerüst, Config, DB/ATTACH, Statusseite | erledigt |
| M2 | Matching-Modul, Skip-Konfiguration, Golden-Tests | erledigt |
| M3 | Refresh mit Änderungserkennung, `sync_data.sh` | erledigt |
| M4 | Web: Jetzt, Stöbern, Werk-Detail, HTMX | erledigt |
| M5 | Review inkl. Overrides und Rückgängig | erledigt |
| M6 | Gespeicherte Suchen und `search_hits` | erledigt |
| M7 | E-Mail-Benachrichtigung | erledigt |
| M8 | Docker, Compose, Backup, GHCR/CI | erledigt |
| M9 | Automatischer EPG-/IMDb-Abgleich (Scheduler, Jobs, Buttons) | erledigt |
| M10 | Inhaltskategorien Film/Serie/Format inkl. manueller Markierungen | erledigt |

## 12. Offene Fragen

1. SMTP-Zugang für den echten Versand (Anbieter, Port, App-Passwort, Absender) – technisch fertig, nur Konfiguration offen.
2. Produktivziel final: Synology oder Docker-Host „Debian“ (beide unterstützt).
3. Darsteller/Regie als Filter (bräuchte `title.principals` + `name.basics`).
4. Optional: `normalize_version` in `imdb.meta` + Neuaufbau, Healthchecks-Ping, TMDB-Poster.

Entschieden: E-Mail statt Telegram, Entwicklung auf `xubuntu-dev` ohne Container,
Standard-Suchen nur `auto`/`confirmed`, Kategorien Film/Serie/Format mit Format
standardmäßig ausgeblendet, Review nur Film/Serie.

## 13. Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Mehrdeutige Titel | Typ/Laufzeit bewerten, Review, Overrides |
| Nicht-fiktionale Formate verwässern Serien/Review | Kategorie „Format“ (Genres) + manuelle Markierungen |
| Restfälle ohne IMDb-Genres | „Kein Treffer“ oder manuell als Format markieren |
| Radiosender im Matching | `channels_ignore.txt`, `skip_block`, Senderbericht |
| Falsche Treffer lösen Mails aus | Mails nur für `auto`/`confirmed`, Sammelmail, Dry-Run |
| SQLite in Netzlaufwerken | nur lokale Dateisysteme, WAL-sichere Backups |
| IMDb-Build: Netz/Platz/9 min | Disk-Preflight, wöchentlich, Log in `data/logs/` |
| Container-Restart während Job | Job als `error` markiert, nächster Lauf holt es nach |
| Watchtower-Update während Build | seltener Fall; DB bleibt durch atomare Builds konsistent |
