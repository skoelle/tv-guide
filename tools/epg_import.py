#!/usr/bin/env python3
"""
epg_import.py: holt XMLTV von TVHeadend und schreibt/aktualisiert eine SQLite-DB.

Nur Standardbibliothek, Python 3.9+. Idempotent: mehrfaches Ausfuehren aktualisiert
bestehende Eintraege (Schluessel: Sender + Startzeit) und legt neue an.

Beispiele:
  ./epg_import.py                                   # Download + Import, DB ./data/epg.db
  ./epg_import.py --file channels.xml               # nur lokale Datei importieren
  ./epg_import.py --user admin --password geheim    # falls TVH Auth verlangt
  ./epg_import.py --keep-xml ./data/xml             # heruntergeladenes XML archivieren
"""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_URL = "http://docker-host-nas.fritz.box:9981/xmltv/channels"

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    number        TEXT,
    icon          TEXT,
    last_seen     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id    TEXT NOT NULL REFERENCES channels(id),
    start_ts      INTEGER NOT NULL,          -- Unix-Epoche (UTC)
    stop_ts       INTEGER NOT NULL,
    start_local   TEXT NOT NULL,             -- Originalstring aus dem XML
    title         TEXT NOT NULL,
    sub_title     TEXT,
    description   TEXT,
    categories    TEXT,                      -- JSON-Liste
    rating        TEXT,                      -- z.B. FSK-Wert
    prod_date     TEXT,                      -- <date>, falls TVH es liefert
    episode_num   TEXT,                      -- JSON-Liste aus <episode-num>
    credits       TEXT,                      -- JSON-Objekt aus <credits>
    star_rating   TEXT,
    icon          TEXT,
    content_hash  TEXT NOT NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    import_id     INTEGER,
    UNIQUE (channel_id, start_ts)
);

CREATE INDEX IF NOT EXISTS idx_events_start    ON events(start_ts);
CREATE INDEX IF NOT EXISTS idx_events_title    ON events(title);
CREATE INDEX IF NOT EXISTS idx_events_first    ON events(first_seen);

CREATE TABLE IF NOT EXISTS imports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    source        TEXT,
    xml_sha1      TEXT,
    xml_bytes     INTEGER,
    channels      INTEGER,
    events_total  INTEGER,
    events_new    INTEGER,
    events_changed INTEGER,
    events_pruned INTEGER
);
"""

CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.I)
DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>]*>", re.I)
XMLTV_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?\s*([+-]\d{4}|Z)?$")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def download(url, user=None, password=None, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "epg-import/1.0"})
    if user:
        token = b64encode(f"{user}:{password or ''}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    log(f"Lade {url}")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    log(f"{len(data) / 1e6:.1f} MB in {time.time() - t0:.1f}s")
    return data


def sanitize(raw: bytes) -> bytes:
    """TVH kuerzt Texte teils mitten in einem UTF-8-Zeichen. Daher tolerant decodieren,
    Steuerzeichen entfernen, Deklaration und DOCTYPE (xmltv.dtd) weg."""
    text = raw.decode("utf-8", errors="replace")
    text = XML_DECL_RE.sub("", text, count=1)
    text = DOCTYPE_RE.sub("", text, count=1)
    text = CTRL_RE.sub("", text)
    return text.encode("utf-8")


def parse_ts(value):
    """XMLTV-Zeit '20261104130000 +0100' -> Unix-Epoche (UTC)."""
    m = XMLTV_TS_RE.match(value.strip())
    if not m:
        raise ValueError(f"Unbekanntes Zeitformat: {value!r}")
    y, mo, d, h, mi, s, tz = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
    if tz in (None, "Z"):
        off = 0
    else:
        sign = 1 if tz[0] == "+" else -1
        off = sign * (int(tz[1:3]) * 3600 + int(tz[3:5]) * 60)
    return int(dt.replace(tzinfo=timezone.utc).timestamp()) - off


def text_of(el, tag):
    c = el.find(tag)
    if c is None or c.text is None:
        return None
    t = c.text.strip()
    return t or None


def parse_credits(el):
    c = el.find("credits")
    if c is None:
        return None
    out = {}
    for child in c:
        if child.text and child.text.strip():
            out.setdefault(child.tag, []).append(child.text.strip())
    return out or None


def parse_xmltv(xml_bytes):
    root = ET.fromstring(xml_bytes)
    channels, events = [], []
    for ch in root.findall("channel"):
        names = [n.text.strip() for n in ch.findall("display-name") if n.text and n.text.strip()]
        icon = ch.find("icon")
        number = next((n for n in names[1:] if n.isdigit()), None)
        channels.append({
            "id": ch.get("id"),
            "name": names[0] if names else ch.get("id"),
            "number": number,
            "icon": icon.get("src") if icon is not None else None,
        })
    for p in root.findall("programme"):
        try:
            start_ts = parse_ts(p.get("start"))
            stop_ts = parse_ts(p.get("stop") or p.get("start"))
        except (ValueError, TypeError, AttributeError):
            continue
        title = text_of(p, "title")
        if not title:
            continue
        cats = [c.text.strip() for c in p.findall("category") if c.text and c.text.strip()]
        rating = None
        r = p.find("rating")
        if r is not None:
            rating = text_of(r, "value")
        eps = [{"system": e.get("system"), "value": (e.text or "").strip()} for e in p.findall("episode-num")]
        icon = p.find("icon")
        events.append({
            "channel_id": p.get("channel"),
            "start_ts": start_ts,
            "stop_ts": stop_ts,
            "start_local": p.get("start"),
            "title": title,
            "sub_title": text_of(p, "sub-title"),
            "description": text_of(p, "desc"),
            "categories": json.dumps(cats, ensure_ascii=False) if cats else None,
            "rating": rating,
            "prod_date": text_of(p, "date"),
            "episode_num": json.dumps(eps, ensure_ascii=False) if eps else None,
            "credits": json.dumps(parse_credits(p), ensure_ascii=False) if parse_credits(p) else None,
            "star_rating": text_of(p.find("star-rating"), "value") if p.find("star-rating") is not None else None,
            "icon": icon.get("src") if icon is not None else None,
        })
    return channels, events


def content_hash(e):
    parts = [e["stop_ts"], e["title"], e["sub_title"], e["description"], e["categories"],
             e["rating"], e["prod_date"], e["episode_num"], e["credits"], e["star_rating"]]
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False, default=str).encode()).hexdigest()


def open_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


def import_into_db(con, channels, events, source, xml_sha1, xml_bytes, prune_days):
    ts = now_iso()
    cur = con.cursor()
    cur.execute("INSERT INTO imports (started_at, source, xml_sha1, xml_bytes) VALUES (?,?,?,?)",
                (ts, source, xml_sha1, xml_bytes))
    import_id = cur.lastrowid

    cur.executemany(
        """INSERT INTO channels (id, name, number, icon, last_seen) VALUES (:id,:name,:number,:icon,:ts)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, number=excluded.number,
             icon=excluded.icon, last_seen=excluded.last_seen""",
        [{**c, "ts": ts} for c in channels],
    )
    known_channels = {c["id"] for c in channels}

    existing = {(r[0], r[1]): r[2] for r in cur.execute("SELECT channel_id, start_ts, content_hash FROM events")}
    new = changed = 0
    for e in events:
        if e["channel_id"] not in known_channels:
            cur.execute("INSERT OR IGNORE INTO channels (id, name, last_seen) VALUES (?,?,?)",
                        (e["channel_id"], e["channel_id"], ts))
            known_channels.add(e["channel_id"])
        h = content_hash(e)
        key = (e["channel_id"], e["start_ts"])
        row = {**e, "hash": h, "ts": ts, "import_id": import_id}
        if key not in existing:
            cur.execute(
                """INSERT INTO events (channel_id,start_ts,stop_ts,start_local,title,sub_title,description,
                     categories,rating,prod_date,episode_num,credits,star_rating,icon,content_hash,
                     first_seen,last_seen,updated_at,import_id)
                   VALUES (:channel_id,:start_ts,:stop_ts,:start_local,:title,:sub_title,:description,
                     :categories,:rating,:prod_date,:episode_num,:credits,:star_rating,:icon,:hash,
                     :ts,:ts,:ts,:import_id)""", row)
            new += 1
        elif existing[key] != h:
            cur.execute(
                """UPDATE events SET stop_ts=:stop_ts,start_local=:start_local,title=:title,sub_title=:sub_title,
                     description=:description,categories=:categories,rating=:rating,prod_date=:prod_date,
                     episode_num=:episode_num,credits=:credits,star_rating=:star_rating,icon=:icon,
                     content_hash=:hash,last_seen=:ts,updated_at=:ts,import_id=:import_id
                   WHERE channel_id=:channel_id AND start_ts=:start_ts""", row)
            changed += 1
        else:
            cur.execute("UPDATE events SET last_seen=?, import_id=? WHERE channel_id=? AND start_ts=?",
                        (ts, import_id, e["channel_id"], e["start_ts"]))

    pruned = 0
    if prune_days is not None:
        cutoff = int(time.time()) - prune_days * 86400
        pruned = cur.execute("DELETE FROM events WHERE stop_ts < ?", (cutoff,)).rowcount

    cur.execute(
        """UPDATE imports SET finished_at=?, channels=?, events_total=?, events_new=?,
             events_changed=?, events_pruned=? WHERE id=?""",
        (now_iso(), len(channels), len(events), new, changed, pruned, import_id))
    con.commit()
    return new, changed, pruned


def main():
    ap = argparse.ArgumentParser(description="TVHeadend XMLTV -> SQLite")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"XMLTV-URL (Default: {DEFAULT_URL})")
    ap.add_argument("--file", help="Lokale XML-Datei statt Download verwenden")
    ap.add_argument("--db", default="data/epg.db", help="Pfad zur SQLite-Datei (Default: data/epg.db)")
    ap.add_argument("--user", help="HTTP-Basic-Auth Benutzer (falls TVH das verlangt)")
    ap.add_argument("--password", help="HTTP-Basic-Auth Passwort")
    ap.add_argument("--timeout", type=int, default=120, help="Download-Timeout in Sekunden")
    ap.add_argument("--keep-xml", metavar="DIR", help="Rohes XML zusaetzlich in DIR ablegen")
    ap.add_argument("--prune-days", type=int, default=None,
                    help="Events loeschen, die vor mehr als N Tagen endeten (Default: nie)")
    args = ap.parse_args()

    if args.file:
        raw = Path(args.file).read_bytes()
        source = f"file:{args.file}"
    else:
        raw = download(args.url, args.user, args.password, args.timeout)
        source = args.url

    sha1 = hashlib.sha1(raw).hexdigest()
    if args.keep_xml:
        d = Path(args.keep_xml)
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"epg_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xml"
        out.write_bytes(raw)
        log(f"XML archiviert: {out}")

    channels, events = parse_xmltv(sanitize(raw))
    log(f"Geparst: {len(channels)} Sender, {len(events)} Sendungen")
    if not events:
        log("Keine Sendungen gefunden, breche ab (DB bleibt unveraendert).")
        return 1

    con = open_db(args.db)
    new, changed, pruned = import_into_db(con, channels, events, source, sha1, len(raw), args.prune_days)
    total = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    log(f"Fertig: {new} neu, {changed} geaendert, {pruned} geloescht, {total} gesamt in {args.db}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
