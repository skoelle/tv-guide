# PLAN: TV-Guide App

Status: v0.2, 2026-11-05
Bezug: SPEC.md (Datenmodell, Matching, Filter, Oberfläche). Diese Datei beschreibt nur die Umsetzung der App.

Änderungen gegenüber v0.1:

- Entwicklung und erster Betrieb auf `xubuntu-dev` (`~/Code/tv-guide`), nicht auf der Synology.
- M7 sendet E-Mails statt Telegram.
- Repo und Paket heißen `tv-guide` bzw. `tvguide`.
- Python 3.12.3 und freier Port 8085 sind bestätigt.
- Neuer Prüfpunkt in M0: liegt `data/` auf einem Netzlaufwerk?

## 1. Ausgangslage

Fertig und getestet (Werkzeuge liefen auf der Synology in `/volume1/work/Xchange/imdb`, die Daten liegen jetzt in `~/Code/tv-guide/data/`):

- `epg_import.py`: TVHeadend-XMLTV nach `data/epg.db` (195 Sender, 49.669 Sendungen, 01.11. bis 29.11.2026).
- `imdb_build.py`: `data/imdb.db` (1.285.899 Titel, 2.781.199 Namen, 493 MB).
- `match_test.py` v2.1: Matching-Prototyp mit Berichten. Er ist die fachliche Vorlage für das Matching-Modul der App.
- `channels_ignore.txt`: Liste der Radio- und Shopping-Sender.
- Scribbles für Jetzt, Stöbern, Review und Suchen.

Messwerte des letzten Matching-Laufs (Referenz für Tests):

| Kennzahl | Wert |
|---|---|
| Betrachtete Sendungen | 28.676 |
| Eindeutige Titel | 4.298 |
| auto / review / none (Titel) | 1.425 / 631 / 1.556 |
| Retro (auto+review) | 42 abgeschlossene Serien, 9 lange Serien, 56 Filme, 70 laufende Serien |

Umgebung (bestätigt): `stefan@xubuntu-dev`, Python 3.12.3, Port 8085 frei. In `data/` liegen `epg.db` (29 MB, mit leerer `-wal` und `-shm`) und `imdb.db` (493 MB). Alle Dateien haben Modus 777, das deutet auf ein eingehängtes Netzlaufwerk oder eine Kopie mit weiten Rechten hin (siehe M0).

## 2. Entscheidungen

| Thema | Entscheidung |
|---|---|
| Start | Reines Python-Programm, gestartet mit `./run.sh` oder `python -m tvguide`. Docker erst am Ende (M8) |
| Ort | `xubuntu-dev`, Projekt in `~/Code/tv-guide`. Produktivbetrieb später als Docker-Container auf der Synology oder dem Docker-Host (M8) |
| Datenfluss | Importwerkzeuge laufen weiter auf der Synology (nahe an TVHeadend). `epg.db` und `imdb.db` werden per rsync nach `xubuntu-dev/data/` kopiert |
| Web | FastAPI, uvicorn, Jinja2, HTMX (Datei lokal ins Repo, kein CDN) |
| DB | SQLite. `epg.db` und `imdb.db` read-only per ATTACH, `app.db` für Zustand |
| Konfiguration | `config.toml` (Pfade, Port, Schwellen, Retro-Fenster, Sendergruppen, SMTP) |
| Zeitzone | Europe/Berlin über `zoneinfo`, intern UTC-Unix-Zeit |
| Zugriff | nur Heimnetz, zunächst ohne Login. Bind auf `0.0.0.0:8085`, damit andere Geräte im LAN zugreifen können |
| Suchen | Standard: nur `auto` und `confirmed`, `review` per Schalter |
| Benachrichtigung | E-Mail per SMTP (Python `smtplib`), austauschbare Schnittstelle für weitere Kanäle |
| Editor in Anleitungen | vim |

Offen, aber nicht blockierend: SMTP-Anbieter und Absender (siehe M7).

## 3. Repo-Struktur

```
tv-guide/
  SPEC.md
  PLAN.md
  README.md
  config.toml
  config.example.toml        # ohne Zugangsdaten, im Git
  run.sh
  requirements.txt
  tools/                     # unverändert, laufen eigenständig
    epg_import.py
    imdb_build.py
    match_test.py
  tvguide/
    __init__.py
    __main__.py              # python -m tvguide  (serve | refresh | match | status | notify)
    config.py
    db.py                    # Verbindungen, ATTACH, Migrationen für app.db
    normalize.py             # identisch zu imdb_build.py
    cleaning.py              # clean_title, candidates, Folgenmarker
    matching.py              # Bewertung, Entscheidung, Overrides
    refresh.py               # neue/geänderte Daten verarbeiten
    search.py                # Filter-JSON nach SQL, gespeicherte Suchen
    notify/
      __init__.py            # Schnittstelle Notifier
      email.py               # SMTP-Versand, Text und HTML
    web/
      app.py                 # FastAPI-Routen
      templates/
      static/                # htmx.min.js, style.css
  tests/
    test_normalize.py
    test_cleaning.py
    test_matching_golden.py
    test_search.py
    test_notify_email.py
    golden/                  # CSV aus echten Läufen
  data/                      # nicht im Git: epg.db, imdb.db, app.db, outbox/, imdb_raw/
```

`data/`, `imdb_raw/`, `*.db`, `match_out/` und `config.toml` stehen in `.gitignore`.

## 4. Meilensteine

Jeder Meilenstein ist einzeln lauffähig und hat ein klares Abnahmekriterium.

### M0: Umgebung auf xubuntu-dev prüfen

- [x] Python 3.12.3 vorhanden.
- [x] Port 8085 frei.
- [x] Prüfen, ob `data/` lokal liegt (`findmnt`/`df`): lokales `ext4`.
- [x] Rechte aufräumen (`644`, kein 777 mehr); `epg.db-wal`/`-shm` nach Checkpoint entfernt.
- [x] Virtuelle Umgebung `.venv` mit `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `pytest`.
- [x] FTS5 verfügbar (SQLite 3.45.1).
- [x] Repo angelegt, `tools/`, `SPEC.md`, `PLAN.md`, `.gitignore`, Remote `origin` (GitHub).

Abnahme: Ein leeres FastAPI-Programm antwortet im Browser vom Notebook aus auf `http://xubuntu-dev:8085/` (oder mit der IP-Adresse).

### M1: Grundgerüst und Daten verbinden

- [x] `config.py` liest `config.toml` (Pfade zu den drei DBs, Port, Schwellen).
- [x] `db.py` öffnet `app.db` und hängt `epg.db` und `imdb.db` mit `mode=ro` an. Migrationen für `app.db` mit einer `schema_version`-Tabelle.
- [x] `python -m tvguide status` zeigt Sender, Sendungen, Zeitraum, letzten Import, IMDb-Datenstand, Shows und Matches.
- [x] Route `GET /status` zeigt dasselbe als Seite.

Abnahme: `status` gibt die bekannten Zahlen aus (195 Sender, 49.669 Sendungen, 1.285.899 IMDb-Titel).

### M2: Matching-Modul

- [x] `normalize.py`, `cleaning.py`, `matching.py` aus `match_test.py` v2.1 übernehmen und in testbare Funktionen aufteilen. Keine Logik im `main`.
- [x] Ignorierliste (`channels_ignore.txt`) und Skip-Regeln (Kategorie, Titel, kurz, Block) als Konfiguration (`[skip]` in `config.toml`).
- [x] Golden-Tests aus echten Läufen: eine CSV mit etwa 100 Zeilen (EPG-Titel, erwarteter IMDb-Titel oder "kein Treffer"). Mindestens: Das A-Team, MacGyver, Magnum, Xena, Hör' mal wer da hämmert, Alle unter einem Dach, Pfarrer Braun: Adel vernichtet (Serie), Polizeiruf 110: Abgründe (review), Stargate, Barbara (Folge 1 von 10), Hubert und Staller (94), Hitnacht (Skip).
- [x] `python -m tvguide match` schreibt `epg_shows`, `show_match`, `event_show` in `app.db`. Läuft nur für neue Shows und für Shows ohne `confirmed` oder Override.
- [x] Overrides haben Vorrang und werden nie überschrieben.

Anmerkungen zur Umsetzung:

- `reason` in `show_match` enthält die Einzelsignale als JSON (SPEC 4).
- Der Match-Lauf reproduziert den Prototyp exakt (4.298 Shows, auto 1.425 / review 631 / none 1.556, 686 per Skip gefiltert, 28.676 betrachtete Sendungen) in wenigen Sekunden.
- Golden-Rohdaten stammen aus einem Referenzlauf von `tools/match_test.py`; der Test hängt bewusst von der Live-`channels_ignore.txt` ab und wird ohne die echten `data/*.db` übersprungen.
- `normalize_version` in `imdb.meta` bleibt bis zum nächsten `imdb.db`-Neubau offen.

Abnahme: Ergebnis stimmt mit dem Prototyp überein (auto, review und none je auf etwa 1 Prozent genau), alle Golden-Tests laufen grün. Laufzeit unter 2 Minuten.

### M3: Refresh

- [x] `python -m tvguide refresh` erkennt geänderte `epg.db` (mtime und `imports`-Tabelle) und führt `match` aus.
- [x] Die Zuordnung Sendung zu Show läuft über den natürlichen Schlüssel `(channel_id, start_ts)`.
- [x] Der Refresh meldet Shows, Match-Status und die Anzahl neuer Sendungen des letzten Imports.
- [x] `rsync`-Hilfsskript `scripts/sync_data.sh` holt `epg.db` (und bei Bedarf `imdb.db`) von der Synology. Es kopiert in eine Temp-Datei und tauscht atomar aus. Vorher (best effort) ein Checkpoint via `ssh`/`sqlite3`.

Anmerkungen:

- Der Refresh-Zustand (`epg_mtime`, `epg_import_id`, `last_refresh`) liegt in der neuen Tabelle `app_meta` (Migration 2, Schema-Version jetzt 2).
- Ein unveränderter Lauf ist billig und gibt nur den aktuellen Stand aus; `--force` erzwingt einen Match-Lauf.
- Ein explizites „geänderte Zuordnungen"-Delta gibt es noch nicht; der Lauf weist Shows und Status aus.

Abnahme: Ein zweiter `epg_import.py`-Lauf mit unveränderten Daten führt zu "0 neu". Eine künstlich entfernte Zuordnung wird beim Refresh wiederhergestellt. Overrides bleiben unverändert.

### M4: Web: Jetzt und Stöbern

- [x] Basislayout, Navigation (Jetzt, Stöbern, Suchen, Review, Status), schlichtes CSS, lokale HTMX-Datei.
- [x] **Jetzt** (Scribble 1): Tabelle "Läuft gerade" und "Heute Abend", Filterleiste mit Chips und Zählern (Typ, Dekade, Sendergruppe, Genre, Zuordnung).
- [x] **Stöbern** (Scribble 2): Karten pro Werk mit nächstem Termin und weiteren Terminen, Sortierung, Chips, Paginierung.
- [x] **Werk-Detail**: Titel, Jahr (bei Serien Start-Ende), Genre, Rating, IMDb-Link, alle Termine im Zeitraum.
- [x] Retro-Filter (Film oder abgeschlossene Serie, 1980 bis 1999, ohne Genres News, Talk, Game-Show, Reality, Doku, Musik, Sport) als Chip; Standard sind laut Absprache alle zugeordneten Werke.
- [x] Sendergruppen (zum Beispiel "Retro": TELE 5, Kabel Eins, RTLNITRO, Pro7 MAXX, SAT.1 Gold, ONE, RTLZWEI) in `config.toml`.
- [x] Footer mit IMDb-Quellenangabe.

Anmerkungen:

- `tvguide/browse.py` baut den Werk-Katalog (Aggregation pro `tconst`), Filter, Facetten und Sortierung; der Katalog wird pro Prozess gecacht (Signatur aus `epg.db`/`imdb.db`-mtime und `app.db`-Inhalt).
- Genres werden über eine EN→DE-Tabelle angezeigt; Poster waren zunächst Platzhalter (siehe M11).
- Review-Treffer erscheinen als „unsicher" und sind per Chip abwählbar.
- Ansichten sind unter `/?at=` reproduzierbar; `/api/now`, `/api/browse`, `/api/works/{tconst}` liefern JSON.

Abnahme: Auf `http://xubuntu-dev:8085/` sind Das A-Team, MacGyver und die Olsenbande-Filme sichtbar, mit richtigem Jahr und Terminen. Seite lädt unter 1 Sekunde.

### M5: Review

- [x] Liste der Shows mit Status `review`, sortiert nach Anzahl Sendungen.
- [x] Aktionen: Vorschlag bestätigen, anderen Kandidaten wählen (Prefix-Suche in `imdb.db`), "kein Treffer".
- [x] Jede Entscheidung schreibt in `overrides` und aktualisiert `show_match`.
- [x] Rückgängig machen der letzten Entscheidung (`override_log`, Migration 3).

Anmerkungen:

- `tvguide/review.py`: Liste, `apply_override`, `undo_last`, `show_key_for` (akzeptiert show_key oder numerische show_id).
- Kandidaten kommen aus `matching.suggestions` (derselbe Score wie im Matching); die Suche läuft über einen Index-Prefixbereich auf `title_names.norm`.
- `load_shows` und der Werk-Katalog sind pro Prozess gecacht; die Review-Seite ist dadurch nach dem ersten Aufruf im zweistelligen Millisekundenbereich.
- `override_log` speichert den vorherigen Override- und `show_match`-Zustand, damit "rückgängig" exakt wiederherstellt.
- API: `GET /api/review`, `POST /api/review/{show_id|show_key}`.

Abnahme: Nach Bestätigung erscheint die Show sofort in Jetzt und Stöbern mit dem gewählten Werk und bleibt nach einem Refresh erhalten.

### M6: Gespeicherte Suchen

- [x] Filter-JSON mit Whietelist-Validierung (nur bekannte Felder und Werte, keine Auswertung von Nutzer-Code).
- [x] "Als Suche speichern" in Jetzt und Stöbern übernimmt die aktuellen Filter.
- [x] Seite "Suchen": Liste, Trefferzahl, "neu seit letztem Lauf", Bearbeiten, Löschen.
- [x] `search_hits` wird bei jedem Refresh fortgeschrieben (neue Treffer, `found_at`).
- [x] Schalter "Review-Treffer einbeziehen" pro Suche (Standard aus).
- [x] Schalter "Benachrichtigen" pro Suche (wirkt erst ab M7).

Anmerkungen:

- Abweichung von SPEC 6.2: kein SQL-Builder, sondern ein validiertes Filter-JSON, das mit derselben Engine wie die Filteransicht (`browse`) ausgewertet wird. Grund: Der Werk-Katalog wird pro `tconst` in Python aggregiert (Retro-Klasse, Genre-Ausschluss); ein zweiter SQL-Pfad würde garantiert auseinanderlaufen. Die Sicherheitseigenschaft (Whitelist, kein Nutzer-Code) bleibt erhalten.
- `tvguide/search.py`: `normalize_filter`, `effective_filters`, CRUD, `run_searches`, `hits_for`; Migration 4 ergänzt `saved_searches.last_new`.
- `run_searches` wird vom Refresh und von `./run.sh searches` aufgerufen.
- API: `GET/POST /api/searches`, `PUT/DELETE /api/searches/{id}`, `GET /api/searches/{id}/hits`.

Abnahme: Eine gespeicherte Suche "Serien der 80er und 90er" liefert dieselben Treffer wie die Filteransicht. Nach einem Import mit neuen Sendungen erscheinen nur die neuen als "neu".

### M7: Benachrichtigungen per E-Mail

Umfang:

- [x] Schnittstelle `Notifier` mit `send(subject, text, html)`. Erste Umsetzung: `EmailNotifier` mit `smtplib` (Standardbibliothek).
- [x] Konfiguration in `config.toml`: `host`, `port`, `security` (`starttls` oder `ssl`), `username`, `from`, `to`. Das Passwort kommt aus der Umgebungsvariable `TVGUIDE_SMTP_PASSWORD` und steht nie im Git.
- [x] Eine Sammelmail pro Refresh. Gliederung nach gespeicherter Suche, je Treffer: Titel, Jahr, Sender, Datum und Uhrzeit, Link zur Werk-Seite der App.
- [x] Format: Text und HTML als `multipart/alternative`, Betreff mit Anzahl der Treffer.
- [x] `notified_at` in `search_hits` verhindert doppelte Mails. Erst nach erfolgreichem Versand setzen.
- [x] Fehlerverhalten: SMTP-Fehler werden protokolliert, `notified_at` bleibt leer, der nächste Lauf versucht es erneut. Kein Absturz des Refresh.
- [x] `python -m tvguide notify --dry-run` schreibt die Mail als `.eml` nach `data/outbox/` und versendet nichts.
- [x] `python -m tvguide notify --test` sendet eine Testmail.
- [ ] Optional: Healthchecks-Ping nach erfolgreichem Lauf.

Anmerkungen:

- `tvguide/notify/`: `Notifier`/`NullNotifier`, `EmailNotifier` und `digest` (`pending_hits`, `render_digest`, `send_notifications`, `send_test`).
- Der Refresh ruft nach `run_searches` automatisch `send_notifications` auf (in `try/except`, damit der Refresh nie scheitert); ohne `[smtp].host` wird nichts versendet und beim nächsten Lauf nachgeholt.
- Mail-Links nutzen `[server].base_url`.
- Healthchecks-Ping bleibt offen (optional).

Testen ohne echten Mailserver: lokaler Test-SMTP-Server (zum Beispiel Mailpit im Docker-Container auf `localhost:1025`), dazu `--dry-run`. Python 3.12 bringt das Modul `smtpd` nicht mehr mit, daher nicht darauf aufbauen.

Offene Frage vor M7: Welcher SMTP-Zugang soll genutzt werden (eigener Mailanbieter, Domain-Mail, Synology-Mailserver)? Davon hängen Port, Verschlüsselung und ein App-Passwort ab.

Abnahme: Eine neue Sendung passend zur Suche löst genau eine E-Mail aus. Ein erneuter Refresh sendet keine weitere. Bei abgeschaltetem SMTP wird beim nächsten Lauf nachgeholt.

### M8: Betrieb

- [x] `Dockerfile` und `docker-compose.yml` (Volume auf lokale Disk, Port, Restart-Policy). `xubuntu-dev` bleibt Entwicklung.
- [x] Zeitplan: Refresh und Benachrichtigung nach dem Import (`scripts/docker_update.sh` per Cron / Synology Task Scheduler).
- [x] `app.db` in die Backup-Strategie aufnehmen (`scripts/backup_appdb.sh`, WAL-sicheres SQLite-Backup).
- [x] README mit Start, Update, Wiederherstellung.

Anmerkungen:

- `Dockerfile` basiert auf `python:3.12-slim`, enthält einen Healthcheck auf `/healthz`; `config.toml` und `data/` werden als Volumes eingebunden.
- `docker-compose.yml`: `restart: unless-stopped`, Port `8085`, `TVGUIDE_SMTP_PASSWORD` aus `.env`.
- `scripts/docker_update.sh` = atomarer rsync + `refresh` im Container; `scripts/backup_appdb.sh` sichert `app.db` konsistent.
- Image `tvguide:test` gebaut und im Container geprüft (`/healthz` ok).

Abnahme: Neustart des Zielsystems startet die App automatisch. Zustand (Overrides, Suchen) bleibt erhalten.

### M9: Automatischer EPG-/IMDb-Abgleich

- [x] Inhaltsbasierte Änderungserkennung: EPG über `imports.xml_sha1`, IMDb über `meta.source_date` + Build-Parameter. Re-Match nur bei echter Änderung.
- [x] Werkzeuge im Image (`COPY tools`), Wrapper `python -m tvguide epg` / `imdb` (Subprozess, danach Refresh).
- [x] Hintergrund-Jobs (`jobs`-Tabelle, Migration 5): genau ein Job gleichzeitig, Log unter `data/logs/`, Recovery verwaister Jobs beim Start.
- [x] In-App-Scheduler (Daemon-Thread): EPG stündlich, IMDb wöchentlich (Default Montag 04:30 Europe/Berlin), aktiv über `TVGUIDE_SCHEDULE_ENABLED=true`.
- [x] Buttons in `/status` („EPG jetzt importieren", „IMDb jetzt neu bauen") + Live-Status via HTMX, API `/api/jobs`.
- [x] Disk-Preflight beim IMDb-Build (~2 GB); `TVGUIDE_TVH_PASSWORD` aus dem Environment.

Anmerkungen:

- Der Scheduler läuft im Webprozess (ein Prozess pro Container); Zustand in `app_meta`, überlebt Neustarts.
- Das Image enthält jetzt `tools/`; das Produktionsimage installiert weiterhin nur `requirements.txt`.
- Offen (optional): Healthchecks-Ping nach erfolgreichem Lauf.

### M10: Inhaltskategorien (Film/Serie/Format)

- [x] Kategorie aus IMDb-Typ + Genres: `movie/tvMovie` → Film; Serien mit News/Talk-Show/Game-Show/Reality-TV/Documentary/Music/Sport/Short → Format; sonst Serie.
- [x] Stöbern/Jetzt: Filter „Kategorie" (Film/Serie/Format), Standard Film+Serie; Formate per Chip sichtbar; korrekte Labels.
- [x] Review zeigt nur Film/Serie (Format-Shows ausgeblendet, Zähler angepasst).
- [x] Manuelle Markierungen (`content_marks`, Migration 6): „Als Format markieren" im Review, „Als Format markieren"/„Als Serie behalten"/„Markierung entfernen" auf der Werk-Seite.
- [x] „Letzte Markierung rückgängig" auf der Review-Seite; automatisch als Format erkannte Werke lassen sich so zurückholen.
- [x] Gespeicherte Suchen um `category` erweitert (Default Film+Serie).

Anmerkungen:

- Der Match-Status bleibt unverändert (`auto/review/...`), die Kategorie ist eine Sicht-/Filter-Dimension; Golden-Test und Prototyp-Zahlen bleiben gültig.
- Restfälle ohne IMDb-Genres bleiben „Serie“ und können weiterhin per „Kein Treffer“ oder manueller Markierung behandelt werden.

### M11: Poster von TMDB

- [x] Auth über `TVGUIDE_TMDB_READ_TOKEN` (Bearer, bevorzugt) oder `TVGUIDE_TMDB_API_KEY`.
- [x] Auflösung per IMDb-ID (`/find/{tconst}`), Film/Serie-Auswahl, Detail-Fallback.
- [x] Vollständige TMDB-Antworten in `tmdb_cache` (app.db), Bilder unter `data/posters/`.
- [x] Migration 7 (`posters`, `tmdb_cache`); lokaler Cache, 6-Monats-Refresh, Retry für fehlende/fehlerhafte Titel.
- [x] CLI `./run.sh posters [--limit N] [--force] [--prune]` und Job/Button in `/status`.
- [x] Poster in Jetzt, Stöbern und Werk-Detail; Fallback bleibt der bestehende Platzhalter.
- [x] Tests mit gemocktem TMDB (kein Netz), Doku und Attribution.

Anmerkungen:

- Der Initiallauf erfolgt nach dem Deployment im Container, nicht auf `xubuntu-dev`:
  `docker compose exec tvguide python -m tvguide posters`.
- Die Rohdaten liegen bewusst vollständig in `tmdb_cache`, damit spätere Funktionen (z. B. Backdrops oder weitere Metadaten) ohne erneute API-Abfrage darauf zugreifen können.
- Poster werden nicht automatisch bei jedem Refresh geladen; der Lauf ist ein eigener Job/CLI-Befehl.

## 5. Tests

- Unit: Normalisierung, Titelbereinigung, Kandidatenbildung, Bewertung.
- Golden: `tests/golden/*.csv` aus echten Läufen. Ein Test schlägt fehl, wenn sich eine auto-Zuordnung unbeabsichtigt ändert.
- Integration: kleine Beispiel-`epg.db` und `imdb.db` (wenige hundert Zeilen) für Refresh, Suche und Review.
- E-Mail: Aufbau der Nachricht (Betreff, Text, HTML), Idempotenz von `notified_at`, Verhalten bei SMTP-Fehler (mit einem Fake-Server oder Mock).
- Ausführung: `python -m pytest -q` im venv.
- Manuell nach jedem Meilenstein: Checkliste im README (Jetzt, Stöbern, Review, Suche öffnen).

## 6. Arbeitsweise

- Ein Meilenstein pro Sitzung, danach Commit mit Tag (`m1`, `m2`, ...).
- Nach M2 bleibt `tools/match_test.py` als Regressionswerkzeug gegen die App-Ergebnisse erhalten.
- Änderungen an `normalize()` verlangen einen Neuaufbau von `imdb.db` (Version in `meta`).
- Bekannte Fehlertypen kommen in `docs/matching-notes.md`: Senderformate vor dem Strich ("Schlager Gold - Bianca"), gleiche Namen mit anderer Produktion, Reihen ohne Serieneintrag (Polizeiruf 110).

## 7. Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| `data/` liegt auf Netzlaufwerk (Modus 777 deutet darauf hin) | M0 prüft das. `app.db` immer lokal. WAL und Netzlaufwerke vertragen sich schlecht |
| `epg.db` wird während einer Abfrage ersetzt | Verbindungen kurz halten, Austausch atomar per `mv`, ATTACH bei geänderter mtime neu aufbauen |
| `epg.db-wal` und `-shm` bei schreibgeschützter Nutzung | `mode=ro` braucht bei WAL-Dateien Schreibrechte auf das Verzeichnis. Notfalls Checkpoint vor dem Kopieren oder `immutable=1` |
| Falsche auto-Zuordnungen lösen falsche Mails aus | Review-Liste, Overrides, Schwellen in `config.toml`, Suchen nur auf auto und confirmed |
| SMTP-Zugangsdaten | Passwort nur per Umgebungsvariable, `config.toml` nicht im Git, Testmail vor dem Scharfschalten |
| Mails landen im Spam | Absender und Empfänger beim selben Anbieter, Betreff sachlich, wenige Mails (Sammelmail) |
| Laufende Serien verwässern "Retro" | Standardfilter auf abgeschlossene Serien und Filme |
| IMDb-Bedingungen | Nur privat, Quellenangabe im Footer, `imdb.db` nicht weitergeben |

## 8. Nächster Schritt

M0 und M1 in einer Sitzung. Dazu brauche ich von dir die Ausgabe von:

```bash
findmnt -T ~/Code/tv-guide/data
df -hT ~/Code/tv-guide/data
python3 -c "import sqlite3;print(sqlite3.sqlite_version);c=sqlite3.connect(':memory:');c.execute('create virtual table t using fts5(x)');print('fts5 ok')"
ls ~/Code/tv-guide
```

Danach schreibe ich das Gerüst mit `status`-Befehl und Statusseite.
