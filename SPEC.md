# SPEC: TV-Guide (Arbeitstitel `tv-guide`)

Status: Entwurf v0.2, 2026-11-05
Änderungen gegenüber v0.1: Benachrichtigung per E-Mail statt Telegram, Entwicklung auf `xubuntu-dev`, Paketname `tvguide`, Matching-Regeln und Schwellen nach den Testläufen (v2.1) präzisiert, Retro-Klassen definiert, offene Fragen aktualisiert.

## 1. Ziel

Ein privater Dienst im Heimnetz, der das TVHeadend-EPG einliest, jede Sendung einem bekannten Film oder einer Serie zuordnet (inklusive Produktionsjahr, Laufzeit der Serie, Genre, Bewertung) und das Programm durchstöberbar macht. Schwerpunkt: alte Spielfilme und alte Serien (80er und 90er), die gerade oder demnächst laufen. Dazu gespeicherte Suchen mit E-Mail-Benachrichtigung.

### 1.1 Nicht-Ziele

- Keine Aufnahmesteuerung (kein DVR-Zugriff, keine Timer).
- Kein öffentlicher Zugriff, keine Mehrbenutzerverwaltung.
- Kein Scraping von IMDb oder anderen Webseiten.
- Keine kommerzielle Nutzung (IMDb-Datasets sind nur für persönliche, nicht kommerzielle Nutzung).
- Kein Dauerbetrieb auf iMac oder Chromebook. Entwicklung und Test laufen auf `xubuntu-dev`, der Produktivbetrieb später als Docker-Container auf der Synology DS918+ oder dem Docker-Host "Debian".

### 1.2 Leitprinzipien

- Download und Datenaufbereitung (EPG, IMDb) sind getrennte Offline-Werkzeuge. Die App liest nur fertige SQLite-Dateien.
- Nutzerzustand (Zuordnungen, Korrekturen, gespeicherte Suchen) liegt nie in den austauschbaren Daten-DBs.
- Lieber eine unsichere Zuordnung zur Prüfung vorlegen als stillschweigend falsch zuordnen.
- Werkzeuge nutzen nur die Python-Standardbibliothek. Die App nutzt wenige, gut gepflegte Pakete.

## 2. Architektur

```
 Synology (nahe an TVHeadend)                    xubuntu-dev (Entwicklung/Test)
 ----------------------------                    ------------------------------
 tools/epg_import.py   --> epg.db   --rsync-->   ~/Code/tv-guide/data/epg.db   (read-only für App)
 tools/imdb_build.py   --> imdb.db  --rsync-->   ~/Code/tv-guide/data/imdb.db  (read-only für App)
 TVHeadend :9981 <-- epg_import.py               tvguide (FastAPI)  --> data/app.db (Lesen/Schreiben)
                                                 E-Mail über SMTP-Server
```

Später (Produktivbetrieb) zieht dieselbe Anordnung in einen Docker-Container auf der Synology oder dem Docker-Host "Debian" um.

Drei SQLite-Dateien mit klarer Trennung:

| Datei | Geschrieben von | Inhalt | Austauschbar? |
|---|---|---|---|
| `epg.db` | `tools/epg_import.py` | Sender und Sendungen (Rohdaten) | Ja, wird bei jedem Import überschrieben oder fortgeschrieben |
| `imdb.db` | `tools/imdb_build.py` | Gefilterte IMDb-Titel und Namen | Ja, wird komplett neu gebaut |
| `app.db` | App | Zuordnungen, Korrekturen, gespeicherte Suchen, Treffer | Nein, enthält Nutzerzustand, wird gesichert |

Die App verbindet `epg.db` und `imdb.db` per `ATTACH DATABASE ... (mode=ro)` read-only. Die Verknüpfung zwischen Sendungen und App-Zustand läuft über den natürlichen Schlüssel `(channel_id, start_ts)`, nicht über die interne `events.id`. So bleibt der Nutzerzustand gültig, wenn `epg.db` ersetzt wird.

Hinweise zu SQLite:

- Alle Dateien liegen auf einem lokalen Dateisystem (auf `xubuntu-dev` ext4). SQLite mit WAL gehört nicht auf SMB- oder NFS-Freigaben.
- Beim Kopieren von `epg.db` vorher den WAL-Zustand abschließen (`PRAGMA wal_checkpoint(TRUNCATE)` oder `sqlite3 ... ".backup"`), in eine Temp-Datei kopieren und atomar per `mv` austauschen.
- Eine schreibgeschützt geöffnete WAL-Datenbank braucht Schreibrechte auf das Verzeichnis (wegen `-shm`). Alternativ nach einem Checkpoint kopieren oder `immutable=1` verwenden.

## 3. Werkzeuge (außerhalb der App)

Alle Werkzeuge bleiben eigenständige Skripte ohne Abhängigkeiten außer Python 3.9+. Sie sind implementiert und auf echten Daten getestet.

### 3.1 `tools/epg_import.py`

- Lädt `http://docker-host-nas.fritz.box:9981/xmltv/channels` (optional Basic Auth).
- Parst tolerant: TVHeadend schneidet Texte teils mitten in einem UTF-8-Zeichen ab. Das Skript decodiert mit Ersatzzeichen, entfernt Steuerzeichen und DOCTYPE und parst dann.
- Upsert nach `(channel_id, start_ts)`, Änderungserkennung über `content_hash`, Spalten `first_seen`, `last_seen`, `updated_at`, `import_id`.
- Protokolliert jeden Lauf in `imports`.
- Optionen: `--file`, `--db`, `--user`, `--password`, `--keep-xml`, `--prune-days`.
- Gemessen: 20,2 MB in 1,9 s, Parsen und Import in rund 14 s, 195 Sender, 49.669 Sendungen.

### 3.2 `tools/imdb_build.py`

- Lädt `title.basics`, `title.akas`, `title.ratings` von `https://datasets.imdbws.com/` und baut daraus `imdb.db`.
- Behält die Typen `movie`, `tvMovie`, `tvSeries`, `tvMiniSeries`. Keine Einzelepisoden (optional `--with-episodes`).
- Aus `title.akas` nur Namen für DE, AT, CH, Sprache `de` und Originaltitel.
- Speichert pro Name eine normalisierte Form (`norm`).
- Optionen: `--min-votes`, `--min-year`, `--types`, `--adult`, `--no-download`, `--force`.
- Gemessen: 1.285.899 Titel, 2.781.199 Namen, 493 MB, Laufzeit rund 9 Minuten (davon rund 5 Minuten für `title.akas`).
- Schreibt Attribution und Datenstand in die Tabelle `meta`.

Tabellen: `titles` (inklusive `start_year`, `end_year`, `runtime_min`, `genres`, `avg_rating`, `num_votes`), `title_names`, `episodes`, `meta`.

### 3.3 `tools/match_test.py` (v2.1)

Matching-Prototyp und späteres Regressionswerkzeug. Liest `epg.db` und `imdb.db` nur lesend und schreibt `matches.csv`, `report.txt`, `channels_report.csv` und `channels_ignore.suggested.txt`. Die Regeln aus Abschnitt 5 sind hier erprobt. Die App übernimmt die Logik in testbare Module.

### 3.4 Normalisierung

Die Funktion `normalize()` ist der Vertrag zwischen IMDb-Namen und EPG-Titeln: Kleinschreibung, `ß` wird `ss`, `ä/ö/ü` werden `ae/oe/ue`, restliche Diakritika entfallen, `&` wird `und`, alle Nicht-Alphanumerik werden zu einem Leerzeichen, Leerzeichen zusammengefasst.

Im Repo liegt die Referenzimplementierung in `tvguide/normalize.py`. Ein Test stellt sicher, dass sie für eine feste Beispielliste dasselbe Ergebnis liefert wie die Kopien in `tools/imdb_build.py` und `tools/match_test.py`. Ändert man die Regeln, muss `imdb.db` neu gebaut werden (Version in `meta.normalize_version`).

## 4. Datenmodell der App (`app.db`)

```sql
-- Eindeutiger Anzeigename der Show aus dem EPG, nach Bereinigung
CREATE TABLE epg_shows (
    id            INTEGER PRIMARY KEY,
    show_key      TEXT NOT NULL UNIQUE,     -- normalize(bereinigter Titel)
    show_name     TEXT NOT NULL,            -- Anzeigename
    first_seen    TEXT NOT NULL
);

-- Zuordnung Show -> IMDb-Titel
CREATE TABLE show_match (
    show_id       INTEGER PRIMARY KEY REFERENCES epg_shows(id),
    tconst        TEXT,                     -- NULL = kein Treffer
    status        TEXT NOT NULL,            -- auto | review | confirmed | rejected | none
    score         REAL,                     -- offener Wert, siehe Abschnitt 5.2
    method        TEXT,                     -- full | prefix | suffix | override
    reason        TEXT,                     -- JSON mit Einzelbewertungen
    updated_at    TEXT NOT NULL
);

-- Zuordnung Sendung -> Show (natürlicher Schlüssel)
CREATE TABLE event_show (
    channel_id    TEXT NOT NULL,
    start_ts      INTEGER NOT NULL,
    show_id       INTEGER NOT NULL REFERENCES epg_shows(id),
    episode_hint  TEXT,                     -- extrahierter Episodentitel oder Marker
    PRIMARY KEY (channel_id, start_ts)
);

-- Manuelle Korrekturen, haben Vorrang vor der Automatik
CREATE TABLE overrides (
    show_key      TEXT PRIMARY KEY,
    tconst        TEXT,                     -- NULL = bewusst ohne Zuordnung
    note          TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE saved_searches (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    filter_json   TEXT NOT NULL,
    include_review INTEGER NOT NULL DEFAULT 0,
    notify        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_run_at   TEXT
);

CREATE TABLE search_hits (
    search_id     INTEGER NOT NULL REFERENCES saved_searches(id),
    channel_id    TEXT NOT NULL,
    start_ts      INTEGER NOT NULL,
    found_at      TEXT NOT NULL,
    notified_at   TEXT,
    PRIMARY KEY (search_id, channel_id, start_ts)
);

CREATE TABLE schema_version (version INTEGER NOT NULL);
```

Die App liest Sendungen aus `epg.db` und IMDb-Daten aus `imdb.db` und speichert nur Beziehungen und Zustand in `app.db`.

## 5. Titelextraktion und Matching

### 5.1 Beobachtungen aus dem echten EPG (Stand 2026-11)

- 49.669 Sendungen, 195 Sender, Zeitraum 01.11. bis 29.11.2026.
- `sub-title` in 66 %, `desc` in 53 %, `category` in 23 %, `rating` (nur FSK) in 6 % der Sendungen.
- Es gibt kein Produktionsjahr, keine Credits, keine Episodennummern. Ein Jahr im `desc` ist selten (rund 4,5 %) und oft nur ein Handlungsjahr.
- Rund 40 % der Sendungen stammen von Radiosendern. Sie werden über `channels_ignore.txt` ausgeschlossen.
- Folgenmarker stehen im Titel: `(S03/E12)`, `(94)`, `(1318)`, `(Folge 1 von 10)`.
- Bei Serien steht der Episodentitel oft im Titelfeld, getrennt durch Strich oder Doppelpunkt ("Pfarrer Braun: Adel vernichtet").
- Programmflächen wie Teleshopping, Nachrichten und Sendeschluss sind keine Treffer.

### 5.2 Pipeline

1. **Senderfilter:** Sender aus `channels_ignore.txt` (Namen oder `re:`-Regex) werden nicht betrachtet.
2. **Bereinigen:** Folgenmarker, `(HD)`, `Teil N`, `Staffel N`, `Wiederholung` entfernen. Ein Jahr in Klammern (`(1985)`) bleibt als Hinweis.
3. **Gruppieren:** Sendungen mit gleichem bereinigtem Titel bilden eine Show. Verschiedene Folgenmarker und Untertitel zählen als verschiedene Folgen.
4. **Vorfilter (Skip):** Kategorie News, Werbung, Sport, Gesellschaft/Politik, Bildung und Vorschulprogramm. Dazu eine Titelliste (Teleshopping, Sendeschluss und ähnliche), Sendungen mit Median unter 8 Minuten (`skip_short`) und Sendungen ohne Kategorie mit mindestens 100 Minuten und mindestens 10 Terminen (`skip_block`, typisch für Radiostrecken).
5. **Kandidaten bilden:** Voller Titel, Präfixe an Trennern (Strich, Doppelpunkt) und als letzte Möglichkeit der Text nach dem Trenner (Suffix). Suffix-Treffer zählen nur, wenn es keinen Treffer für den vollen Titel oder einen Präfix gibt.
6. **Kandidatensuche in `imdb.db`:** exakter Treffer auf `title_names.norm`. Eine unscharfe Suche (FTS5, Trigramm) ist für spätere Verbesserungen vorgesehen.
7. **Bewerten** (Score, Summe gewichteter Signale, nicht gedeckelt):
   - Name: voller Titel 1,0; Präfix 0,55 bis 0,9 je nach Anteil der Wörter, bei Serien mit mindestens zwei Wörtern im Präfix mindestens 0,85; Suffix 0,45; sehr kurze Namen (bis 4 Zeichen) minus 0,2.
   - Typ gegen Laufzeit des Termins: Film ab 75 Minuten plus, unter 45 Minuten stark minus, bei Präfix-Treffern auf Filme minus; Serie bis 65 Minuten plus, ab 100 Minuten minus, bei Präfix plus.
   - Serienerkennung: mindestens 4 Termine mit mindestens 2 verschiedenen Folgen spricht für Serie.
   - Laufzeit des Films gegen Termindauer (mit Werbung bis etwa 70 % länger).
   - Beliebtheit: bis plus 0,1 nach Stimmenzahl; unter 50 Stimmen minus 0,15; Ein-Wort-Titel unter 200 Stimmen minus 0,2.
   - Jahr im `desc` gleich Startjahr: plus 0,1. Jahr im Titel in Klammern: plus 0,15.
   - Nahezu gleich gute Alternative mit anderem Titel (Abstand unter 0,05): minus 0,15.
8. **Entscheiden:** Score ab 1,0 ist `auto`, ab 0,6 `review`, darunter `none`. Beide Schwellen stehen in `config.toml`. Zur Anzeige gibt es `conf = min(1, score / 1,3)`.
9. **Overrides** aus `overrides` gelten immer zuerst und werden nie überschrieben.

### 5.3 Retro-Klassen

Aus `start_year`, `end_year`, Typ und Genre (ohne News, Talk-Show, Game-Show, Reality-TV, Documentary, Music, Sport, Short) und dem Fenster `retro_from` bis `retro_to` (Standard 1980 bis 1999):

| Klasse | Bedingung |
|---|---|
| `film` | Typ movie oder tvMovie, Startjahr im Fenster |
| `serie_abgeschlossen` | Serie, Startjahr im Fenster, `end_year` bis `retro_end_max` (Standard 2009) |
| `serie_lief_laenger` | Serie, Startjahr im Fenster, `end_year` nach `retro_end_max` |
| `serie_laufend` | Serie, Startjahr im Fenster, kein `end_year` (laufend oder unbekannt) |

Der Standardfilter "Retro" zeigt `film` und `serie_abgeschlossen`. `serie_lief_laenger` und `serie_laufend` sind per Schalter zuschaltbar.

### 5.4 Cache und Wiederholung

Das Matching arbeitet pro Show (`epg_shows`), nicht pro Sendung. Ein bestätigter oder abgelehnter Treffer wird nicht neu berechnet. Das Matching läuft nach jedem Import nur für neue Shows und für Shows mit Status `review` oder `none`, wenn sich `imdb.db` geändert hat.

### 5.5 Ergebnisse des Prototyps (Referenz)

| Kennzahl | Wert |
|---|---|
| Betrachtete Sendungen | 28.676 (von 49.669, Rest ignorierte Sender) |
| Eindeutige Titel | 4.298 |
| auto / review / none | 1.425 / 631 / 1.556 Titel |
| Lange Sendungen (ab 75 Minuten) | 564 auto, 241 review, 224 none |
| Retro (auto und review) | 42 abgeschlossene Serien, 9 lange Serien, 56 Filme, 70 laufende Serien |

Bekannte Fehlertypen: Senderformate vor dem Strich ("Schlager Gold - Bianca"), gleiche Namen mit anderer Produktion, Reihen ohne eigenen Serieneintrag (zum Beispiel "Polizeiruf 110: Abgründe" als Film), Dokureihen, die IMDb als Serie führt. Diese Fälle werden über Review und Overrides gelöst.

## 6. Filter und gespeicherte Suchen

### 6.1 Filterfelder

| Feld | Quelle |
|---|---|
| Titel, Untertitel, Beschreibung (Volltext, Regex) | `epg.db` |
| Sender, Sendergruppe | `epg.db`, Konfiguration |
| Zeit: Datum, Wochentag, Uhrzeit, Länge | `epg.db` |
| EPG-Kategorie, FSK | `epg.db` |
| Typ (Film, Serie, Miniserie), laufend oder beendet | `imdb.db` |
| Produktionsjahr, Dekade, Serienende | `imdb.db` |
| Genre, Rating, Stimmenzahl | `imdb.db` |
| Retro-Klasse | berechnet |
| Match-Status (auto, review, confirmed) | `app.db` |
| Wiederholung, "neu im Programm" (`first_seen`) | `epg.db` |

### 6.2 Filterformat (JSON)

```json
{
  "name": "Serien der 80er und 90er",
  "match": {
    "type": ["tvSeries", "tvMiniSeries"],
    "year": {"from": 1980, "to": 1999},
    "ended_by": 2009,
    "match_status": ["auto", "confirmed"]
  },
  "channels": {"include_groups": ["retro"], "exclude": []},
  "time": {"weekdays": [1,2,3,4,5,6,7], "from": "00:00", "to": "23:59", "min_duration": 20},
  "text": {"title_regex": null, "contains": []},
  "exclude": {"genres": ["Reality-TV", "Talk-Show", "News"]}
}
```

Der Filter wird in eine parametrisierte SQL-Abfrage übersetzt. Es gibt keine Evaluierung von Nutzer-Code. Feldnamen und Operatoren kommen aus einer festen Whitelist.

## 7. Oberfläche und API

Technik: FastAPI, Jinja2-Templates, HTMX (lokal im Repo), SQLite (`sqlite3`). Kein separates Frontend-Build. Alle Seiten funktionieren auf dem Handy im Heimnetz.

### 7.1 Ansichten (nach den Scribbles)

- **Jetzt und demnächst** (Startseite): laufende und heutige Sendungen, Filterleiste mit Chips und Zählern.
- **Stöbern:** Karten pro Werk mit nächstem Termin und weiteren Terminen. Markierung "nur 1 Termin" für Seltenes.
- **Werk-Detail:** Titel, Jahr (bei Serien Start bis Ende), Genre, Rating, IMDb-Link, alle Ausstrahlungen im Zeitraum.
- **Review:** Shows mit Status `review`, Bestätigen, Ablehnen oder Titel manuell wählen. Das Ergebnis geht in `overrides`.
- **Gespeicherte Suchen:** anlegen aus dem aktuellen Filter, Treffer ansehen, Benachrichtigung ein oder aus.
- **Status:** Datenstand von `epg.db` (letzter Import, Zeitraum) und `imdb.db` (Datenstand, Build-Datum), Anzahl offener Reviews.

### 7.2 API (Auszug)

| Methode und Pfad | Zweck |
|---|---|
| `GET /api/now` | laufende Sendungen |
| `GET /api/browse` | gefilterte Sendungen mit Facetten |
| `GET /api/works/{tconst}` | Werk mit Terminen |
| `GET /api/review` | offene Zuordnungen |
| `POST /api/review/{show_id}` | bestätigen, ablehnen oder neu zuordnen |
| `GET/POST/PUT/DELETE /api/searches` | gespeicherte Suchen |
| `GET /api/searches/{id}/hits` | Treffer |
| `GET /api/status` | Datenstand und Gesundheit |

## 8. Benachrichtigungen per E-Mail

- Nach jedem Refresh läuft jede gespeicherte Suche mit `notify=1`. Neue Treffer (noch nicht in `search_hits` mit `notified_at`) lösen eine Nachricht aus.
- Kanal: E-Mail über SMTP (`smtplib`), hinter der Schnittstelle `Notifier`. Weitere Kanäle (zum Beispiel Telegram) lassen sich später ergänzen.
- Eine Sammelmail pro Refresh, gegliedert nach Suche. Je Treffer: Titel, Jahr, Sender, Datum und Uhrzeit, Link zur Werk-Seite.
- Format: `multipart/alternative` mit Text und HTML. Betreff mit Trefferzahl.
- Konfiguration: `smtp_host`, `smtp_port`, `security` (`starttls` oder `ssl`), `username`, `from`, `to`. Das Passwort kommt aus der Umgebungsvariable `TVGUIDE_SMTP_PASSWORD`.
- `notified_at` wird erst nach erfolgreichem Versand gesetzt. Bei Fehlern wird der Versand beim nächsten Lauf wiederholt.
- `--dry-run` schreibt die Mail als `.eml` nach `data/outbox/`. `--test` sendet eine Testmail.
- Standardmäßig lösen nur Treffer mit Status `auto` und `confirmed` eine Mail aus. `review` ist pro Suche zuschaltbar.
- Optional: Healthchecks-Ping nach erfolgreichem Lauf (Healthchecks läuft bereits im Netz).

## 9. Betrieb

### 9.1 Repository-Struktur

```
tv-guide/
  SPEC.md
  PLAN.md
  README.md
  config.toml            # nicht im Git
  config.example.toml
  run.sh
  requirements.txt
  tools/
    epg_import.py
    imdb_build.py
    match_test.py
  tvguide/
    __main__.py          # serve | refresh | match | status | notify
    config.py  db.py  normalize.py  cleaning.py  matching.py
    refresh.py  search.py
    notify/              # Schnittstelle und E-Mail
    web/                 # app.py, templates, static
  tests/
  data/                  # nicht im Git: epg.db, imdb.db, app.db, outbox/, imdb_raw/
```

`data/`, `imdb_raw/`, `*.db`, `match_out/` und `config.toml` stehen in `.gitignore`. IMDb-Rohdaten und `imdb.db` gehören nicht ins Git (Lizenz, Größe).

### 9.2 Entwicklung und Deployment

- Entwicklung und Test: `xubuntu-dev`, `~/Code/tv-guide`, Python 3.12.3, SQLite 3.45.1 mit FTS5, Port 8085, Start mit `./run.sh`.
- Datenfluss: Importwerkzeuge laufen auf der Synology. `epg.db` und `imdb.db` werden nach `xubuntu-dev/data/` kopiert (atomar, siehe Abschnitt 2).
- Produktivbetrieb später: ein Docker-Container (`python:3.12-slim`) auf der Synology oder dem Docker-Host "Debian", Volume auf einer lokalen Disk, kein Netzwerk-Share.
- Zeitplan: Import, dann Refresh, dann Benachrichtigung (Task Scheduler oder Cron).
- Sicherung: `app.db` in die bestehende Backup-Strategie aufnehmen. `epg.db` und `imdb.db` sind reproduzierbar.
- Editor in allen Anleitungen: vim.

### 9.3 Test- und Entwicklungsumgebung

- Kleine Beispiel-`epg.db` und `imdb.db` für Tests. Der Entwicklungsrechner führt keinen EPG-Download und keinen IMDb-Build aus.
- Tests: Normalisierung, Titelbereinigung, Matching (Golden-CSV aus echten Läufen), Filter-zu-SQL, Refresh, E-Mail-Aufbau und Idempotenz.
- E-Mail-Test mit `--dry-run` und einem lokalen Test-SMTP-Server (zum Beispiel Mailpit). Das Modul `smtpd` entfällt ab Python 3.12.

## 10. Lizenz und Datenschutz

- IMDb-Daten nur für persönliche, nicht kommerzielle Nutzung, mit Quellenangabe. Die Attribution steht in `meta` und im Seitenfuß der Oberfläche.
- Keine Weitergabe von `imdb.db`. Kein Zugriff von außerhalb des Heimnetzes.
- SMTP-Zugangsdaten nur per Umgebungsvariable, nie im Git.
- TMDB wird zunächst nicht verwendet. Falls später Poster ergänzt werden, vorher die TMDB-Bedingungen prüfen (Caching und Attribution).

## 11. Phasenplan

| Phase | Inhalt | Ergebnis |
|---|---|---|
| 0 | Werkzeuge `epg_import.py`, `imdb_build.py`, `match_test.py` | `epg.db`, `imdb.db`, Matching-Prototyp (erledigt) |
| 1 | Gerüst, Matching-Modul, Refresh (M0 bis M3) | Zuordnungen in `app.db` |
| 2 | Web: Jetzt, Stöbern, Werk-Detail, Review (M4, M5) | nutzbarer Stöberer |
| 3 | Gespeicherte Suchen (M6) | wiederkehrende Suchen |
| 4 | E-Mail-Benachrichtigung (M7) | Mail bei neuen Treffern |
| 5 | Betrieb (M8), optional Credits (Regie, Darsteller), TMDB-Poster | Docker, Zeitplan, Komfort |

### 11.1 Messbare Ziele

- Die App reproduziert die Prototyp-Zahlen aus 5.5 auf etwa 1 Prozent genau.
- Weniger als 3 % falsche Zuordnungen in einer Stichprobe von 100 Titeln mit Status `auto`.
- Refresh eines Monatsimports in unter 2 Minuten.
- Seitenaufbau von Jetzt und Stöbern unter 1 Sekunde.

## 12. Offene Fragen

1. Welcher SMTP-Zugang wird für M7 genutzt (Anbieter, Port, App-Passwort, Absender)?
2. Welche Sender gehören zur Gruppe "Retro"? (Vorschlag: TELE 5, Kabel Eins, RTLNITRO, Pro7 MAXX, SAT.1 Gold, ONE, RTLZWEI.)
3. Sollen `serie_laufend` und `serie_lief_laenger` im Standardfilter ausgeblendet bleiben? (Vorschlag: ja.)
4. Wohin zieht der Produktivbetrieb: Synology oder Docker-Host "Debian"?
5. Sollen Darsteller und Regie als Filter dazukommen (benötigt zusätzliche IMDb-Dateien, `title.principals` und `name.basics`)?

Entschieden: Benachrichtigung per E-Mail, Entwicklung auf `xubuntu-dev`, Standard für Suchen nur `auto` und `confirmed`, Start als Python-Programm.

## 13. Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Mehrdeutige Titel (zum Beispiel "Stargate") | Typ und Laufzeit bewerten, Review-Liste, Overrides |
| Titel- und Episoden-Mischung im EPG | Bereinigung, Präfix-Extraktion, Show-Ebene statt Event-Ebene |
| Radiosender verschmutzen das Matching | `channels_ignore.txt`, `skip_block`, Senderbericht |
| EPG ohne Jahr, dünne Beschreibungen | Anreicherung über IMDb, Score sichtbar machen |
| IMDb-Format oder Bedingungen ändern sich | `meta` mit Datenstand, Build-Skript isoliert |
| SQLite-Dateien beim Kopieren inkonsistent | atomarer Austausch, Checkpoint oder `.backup`, nur lokale Dateisysteme |
| Falsche Treffer lösen unnötige Mails aus | Mails nur für `auto` und `confirmed`, Sammelmail, Dry-Run |
| Mails landen im Spam oder gehen verloren | Testmail, Wiederholung bei Fehler, Sammelmail statt Einzelmails |
| Disk auf `xubuntu-dev` knapp (82 % belegt, 8,9 GB frei) | `imdb_raw/` nicht mitkopieren, nur die fertigen DBs |
