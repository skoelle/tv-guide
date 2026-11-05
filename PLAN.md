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
- [ ] Prüfen, ob `data/` lokal liegt: `findmnt -T ~/Code/tv-guide/data` und `df -hT ~/Code/tv-guide/data`. Bei `cifs` oder `nfs`: SQLite-Dateien (vor allem `app.db`) lokal ablegen, nicht auf dem Netzlaufwerk. `epg.db` und `imdb.db` dürfen als Kopie lokal liegen.
- [ ] Rechte aufräumen: `chmod 644 data/*.db` (777 ist unnötig). `epg.db-wal` und `epg.db-shm` sind Reste des Matching-Laufs und können nach `PRAGMA wal_checkpoint(TRUNCATE)` bleiben oder gelöscht werden, solange kein Prozess die DB offen hat.
- [ ] Virtuelle Umgebung: `python3 -m venv .venv`, `.venv/bin/pip install fastapi uvicorn jinja2 python-multipart pytest`. Falls `venv` fehlt: `sudo apt install python3-venv`.
- [ ] FTS5 prüfen: `python3 -c "import sqlite3;c=sqlite3.connect(':memory:');c.execute('create virtual table t using fts5(x)')"`.
- [ ] Repo anlegen, Skripte nach `tools/`, `SPEC.md` und `PLAN.md` ablegen, `git init`, `.gitignore` setzen.

Abnahme: Ein leeres FastAPI-Programm antwortet im Browser vom Notebook aus auf `http://xubuntu-dev:8085/` (oder mit der IP-Adresse).

### M1: Grundgerüst und Daten verbinden

- [ ] `config.py` liest `config.toml` (Pfade zu den drei DBs, Port, Schwellen).
- [ ] `db.py` öffnet `app.db` und hängt `epg.db` und `imdb.db` mit `mode=ro` an. Migrationen für `app.db` (Tabellen aus SPEC 4) mit einer `schema_version`-Tabelle.
- [ ] `python -m tvguide status` zeigt: Anzahl Sender und Sendungen, Zeitraum, letzter Import, IMDb-Datenstand, Anzahl Shows und Matches.
- [ ] Route `GET /status` zeigt dasselbe als Seite.

Abnahme: `status` gibt die bekannten Zahlen aus (195 Sender, 49.669 Sendungen, 1.285.899 IMDb-Titel).

### M2: Matching-Modul

- [ ] `normalize.py`, `cleaning.py`, `matching.py` aus `match_test.py` v2.1 übernehmen und in testbare Funktionen aufteilen. Keine Logik im `main`.
- [ ] Ignorierliste (`channels_ignore.txt`) und Skip-Regeln (Kategorie, Titel, kurz, Block) als Konfiguration.
- [ ] Golden-Tests aus echten Läufen: eine CSV mit etwa 100 Zeilen (EPG-Titel, erwarteter IMDb-Titel oder "kein Treffer"). Mindestens: Das A-Team, MacGyver, Magnum, Xena, Hör' mal wer da hämmert, Alle unter einem Dach, Pfarrer Braun: Adel vernichtet (Serie), Polizeiruf 110: Abgründe (review), Stargate, Barbara (Folge 1 von 10), Hubert und Staller (94), Hitnacht (Skip).
- [ ] `python -m tvguide match` schreibt `epg_shows`, `show_match`, `event_show` in `app.db`. Läuft nur für neue Shows und für Shows ohne `confirmed` oder Override.
- [ ] Overrides haben Vorrang und werden nie überschrieben.

Abnahme: Ergebnis stimmt mit dem Prototyp überein (auto, review und none je auf etwa 1 Prozent genau), alle Golden-Tests laufen grün. Laufzeit unter 2 Minuten.

### M3: Refresh

- [ ] `python -m tvguide refresh` erkennt geänderte `epg.db` (mtime und `imports`-Tabelle) und führt `match` aus.
- [ ] Die Zuordnung Sendung zu Show läuft über den natürlichen Schlüssel `(channel_id, start_ts)`.
- [ ] Der Refresh meldet neue Shows, geänderte Zuordnungen und Anzahl neuer Sendungen (`first_seen`).
- [ ] `rsync`-Hilfsskript `sync_data.sh` holt `epg.db` (und bei Bedarf `imdb.db`) von der Synology. Es kopiert in eine Temp-Datei und tauscht atomar aus. Vorher auf der Synology ein Checkpoint (`sqlite3 epg.db "PRAGMA wal_checkpoint(TRUNCATE);"`) oder `.backup` nutzen.

Abnahme: Ein zweiter `epg_import.py`-Lauf mit unveränderten Daten führt zu "0 neu". Eine künstlich entfernte Zuordnung wird beim Refresh wiederhergestellt. Overrides bleiben unverändert.

### M4: Web: Jetzt und Stöbern

- [ ] Basislayout, Navigation (Jetzt, Stöbern, Suchen, Review, Status), schlichtes CSS, lokale HTMX-Datei.
- [ ] **Jetzt** (Scribble 1): Tabelle "Läuft gerade" und "Heute Abend", Filterleiste mit Chips und Zählern (Typ, Dekade, Sendergruppe, Genre, Zuordnung).
- [ ] **Stöbern** (Scribble 2): Karten pro Werk mit nächstem Termin und weiteren Terminen, Sortierung, Chips.
- [ ] **Werk-Detail**: Titel, Jahr (bei Serien Start-Ende), Genre, Rating, IMDb-Link, alle Termine im Zeitraum.
- [ ] Retro-Standardfilter: Typ Film oder abgeschlossene Serie, 1980 bis 1999, ohne Genres News, Talk, Game-Show, Reality, Doku, Musik, Sport. Laufende Serien sind per Chip zuschaltbar.
- [ ] Sendergruppen (zum Beispiel "Retro": TELE 5, Kabel Eins, RTLNITRO, Pro7 MAXX, SAT.1 Gold, ONE, RTLZWEI) in `config.toml`.
- [ ] Footer mit IMDb-Quellenangabe.

Abnahme: Auf `http://xubuntu-dev:8085/` sind Das A-Team, MacGyver und die Olsenbande-Filme sichtbar, mit richtigem Jahr und Terminen. Seite lädt unter 1 Sekunde.

### M5: Review

- [ ] Liste der Shows mit Status `review`, sortiert nach Anzahl Sendungen.
- [ ] Aktionen: Vorschlag bestätigen, anderen Kandidaten wählen (Suche in `imdb.db`), "kein Treffer".
- [ ] Jede Entscheidung schreibt in `overrides` und aktualisiert `show_match`.
- [ ] Rückgängig machen (letzte 20 Entscheidungen).

Abnahme: Nach Bestätigung erscheint die Show sofort in Jetzt und Stöbern mit dem gewählten Werk und bleibt nach einem Refresh erhalten.

### M6: Gespeicherte Suchen

- [ ] Filter-JSON nach SPEC 6.2 und ein SQL-Builder mit Whitelist (nur bekannte Felder und Operatoren, ausschließlich Parameter, keine String-Konkatenation).
- [ ] "Als Suche speichern" in Jetzt und Stöbern übernimmt die aktuellen Filter.
- [ ] Seite "Suchen": Liste, Trefferzahl, "neu seit letztem Lauf", Bearbeiten, Löschen.
- [ ] `search_hits` wird bei jedem Refresh fortgeschrieben (neue Treffer, `found_at`).
- [ ] Schalter "Review-Treffer einbeziehen" pro Suche (Standard aus).
- [ ] Schalter "Benachrichtigen" pro Suche (wirkt erst ab M7).

Abnahme: Eine gespeicherte Suche "Serien der 80er und 90er" liefert dieselben Treffer wie die Filteransicht. Nach einem Import mit neuen Sendungen erscheinen nur die neuen als "neu".

### M7: Benachrichtigungen per E-Mail

Umfang:

- [ ] Schnittstelle `Notifier` mit `send(subject, text, html)`. Erste Umsetzung: `EmailNotifier` mit `smtplib` (Standardbibliothek).
- [ ] Konfiguration in `config.toml`: `smtp_host`, `smtp_port`, `security` (`starttls` oder `ssl`), `username`, `from`, `to` (eine oder mehrere Adressen). Das Passwort kommt aus der Umgebungsvariable `TVGUIDE_SMTP_PASSWORD` und steht nie im Git.
- [ ] Eine Sammelmail pro Refresh (nicht pro Suche und nicht pro Treffer). Gliederung nach gespeicherter Suche, je Treffer: Titel, Jahr, Sender, Datum und Uhrzeit, Link zur Werk-Seite der App.
- [ ] Format: Text und HTML als `multipart/alternative`, Betreff mit Anzahl der Treffer (zum Beispiel "TV-Guide: 3 neue Treffer").
- [ ] `notified_at` in `search_hits` verhindert doppelte Mails. Erst nach erfolgreichem Versand setzen.
- [ ] Fehlerverhalten: SMTP-Fehler werden protokolliert, `notified_at` bleibt leer, der nächste Lauf versucht es erneut. Kein Absturz des Refresh.
- [ ] `python -m tvguide notify --dry-run` schreibt die Mail als `.eml` nach `data/outbox/` und versendet nichts.
- [ ] `python -m tvguide notify --test` sendet eine Testmail.
- [ ] Optional: Healthchecks-Ping nach erfolgreichem Lauf.

Testen ohne echten Mailserver: lokaler Test-SMTP-Server (zum Beispiel Mailpit im Docker-Container auf `localhost:1025`), dazu `--dry-run`. Python 3.12 bringt das Modul `smtpd` nicht mehr mit, daher nicht darauf aufbauen.

Offene Frage vor M7: Welcher SMTP-Zugang soll genutzt werden (eigener Mailanbieter, Domain-Mail, Synology-Mailserver)? Davon hängen Port, Verschlüsselung und ein App-Passwort ab.

Abnahme: Eine neue Sendung passend zur Suche löst genau eine E-Mail aus. Ein erneuter Refresh sendet keine weitere. Bei abgeschaltetem SMTP wird beim nächsten Lauf nachgeholt.

### M8: Betrieb

- [ ] `Dockerfile` und `docker-compose.yml` (Volume auf lokale Disk, Port, Restart-Policy). Zielplattform nach Wahl: Synology oder Docker-Host "Debian". `xubuntu-dev` bleibt Entwicklung.
- [ ] Zeitplan: Refresh und Benachrichtigung nach dem Import (Task Scheduler der Synology oder Cron im Container).
- [ ] `app.db` in die bestehende Backup-Strategie aufnehmen.
- [ ] README mit Start, Update, Wiederherstellung.

Abnahme: Neustart des Zielsystems startet die App automatisch. Zustand (Overrides, Suchen) bleibt erhalten.

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
