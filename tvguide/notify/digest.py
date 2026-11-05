"""Sammelmail pro Refresh: offene Treffer (search_hits mit notify=1, notified_at NULL)."""
from __future__ import annotations

import html as html_module
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..config import Config
from . import Notifier
from .email import EmailNotifier, build_message, smtp_settings

_PENDING_SQL = """
SELECT sh.search_id, ss.name AS search_name, sh.channel_id, sh.start_ts,
       COALESCE(c.name, sh.channel_id) AS channel,
       e.title AS epg_title, sh.found_at,
       s.show_name, sm.tconst, sm.status,
       t.primary_title, t.start_year
FROM search_hits sh
JOIN saved_searches ss ON ss.id = sh.search_id
LEFT JOIN epg.events e ON e.channel_id = sh.channel_id AND e.start_ts = sh.start_ts
LEFT JOIN epg.channels c ON c.id = sh.channel_id
LEFT JOIN event_show es ON es.channel_id = sh.channel_id AND es.start_ts = sh.start_ts
LEFT JOIN epg_shows s ON s.id = es.show_id
LEFT JOIN show_match sm ON sm.show_id = es.show_id
LEFT JOIN imdb.titles t ON t.tconst = sm.tconst
WHERE ss.notify = 1 AND sh.notified_at IS NULL AND (sm.status IS NULL OR sm.status IN ('auto', 'confirmed'))
ORDER BY ss.name, sh.start_ts
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _link(cfg: Config, tconst: str | None) -> str:
    if not tconst:
        return ""
    return f"{cfg.base_url}/work/{tconst}" if cfg.base_url else f"/work/{tconst}"


def pending_hits(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute(_PENDING_SQL)]


def _fmt_dt(ts: int, cfg: Config) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(cfg.timezone)).strftime("%a, %d.%m. %H:%M")


def render_digest(hits: list[dict], cfg: Config) -> tuple[str, str]:
    """Baut (Text, HTML) der Sammelmail, gegliedert nach Suche."""
    groups: dict[str, list[dict]] = {}
    for h in hits:
        groups.setdefault(h["search_name"], []).append(h)

    tlines = []
    for name, items in groups.items():
        tlines.append(f"{name} ({len(items)}):")
        for h in items:
            title = h["show_name"] or h["primary_title"] or h["epg_title"] or "?"
            year = f" ({h['start_year']})" if h.get("start_year") else ""
            link = (" " + _link(cfg, h["tconst"])) if h["tconst"] else ""
            tlines.append(f"  - {title}{year}, {_fmt_dt(h['start_ts'], cfg)}, {h['channel']}{link}")
        tlines.append("")
    text = "\n".join(tlines).strip() or "Keine neuen Treffer."

    parts = ["<h2>Neue Treffer</h2>"]
    for name, items in groups.items():
        parts.append(f"<h3>{html_module.escape(name)} ({len(items)})</h3><ul>")
        for h in items:
            title = html_module.escape(h["show_name"] or h["primary_title"] or h["epg_title"] or "?")
            year = f" ({h['start_year']})" if h.get("start_year") else ""
            meta = f"{_fmt_dt(h['start_ts'], cfg)}, {html_module.escape(h['channel'])}"
            if h["tconst"]:
                link = html_module.escape(_link(cfg, h["tconst"]))
                parts.append(f'<li><a href="{link}">{title}</a>{year}<br><span style="color:#666">{meta}</span></li>')
            else:
                parts.append(f"<li>{title}{year}<br><span style=\"color:#666\">{meta}</span></li>")
        parts.append("</ul>")
    return text, "\n".join(parts)


def _write_eml(cfg: Config, settings: dict, subject: str, text: str, html: str) -> str:
    outbox = cfg.app_db.parent / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = outbox / f"tvguide_{stamp}.eml"
    path.write_bytes(build_message(settings, subject, text, html).as_bytes())
    return str(path)


def send_notifications(con: sqlite3.Connection, cfg: Config, notifier: Notifier | None = None,
                       *, dry_run: bool = False) -> dict:
    """Verschickt eine Sammelmail fuer alle offenen, benachrichtigungspflichtigen Treffer.

    `notified_at` wird erst nach erfolgreichem Versand gesetzt. Fehler werden gemeldet,
    aber nicht geworfen, damit ein Refresh nie daran scheitert.
    """
    settings = smtp_settings(cfg)
    hits = pending_hits(con)
    if not hits:
        return {"pending": 0, "sent": 0}
    subject = f"TV-Guide: {len(hits)} neue Treffer"
    text, html = render_digest(hits, cfg)

    if dry_run:
        path = _write_eml(cfg, settings, subject, text, html)
        return {"pending": len(hits), "sent": 0, "dry_run": True, "file": path}

    if notifier is None:
        if not settings["host"]:
            return {"pending": len(hits), "sent": 0, "skipped": "SMTP nicht konfiguriert"}
        notifier = EmailNotifier(settings)

    try:
        notifier.send(subject, text, html)
    except Exception as exc:  # noqa: BLE001 - Refresh darf nicht abbrechen
        return {"pending": len(hits), "sent": 0, "error": str(exc)}

    now = _now()
    con.executemany(
        "UPDATE search_hits SET notified_at=? WHERE search_id=? AND channel_id=? AND start_ts=? AND notified_at IS NULL",
        [(now, h["search_id"], h["channel_id"], h["start_ts"]) for h in hits])
    con.commit()
    return {"pending": len(hits), "sent": len(hits)}


def send_test(cfg: Config, notifier: Notifier | None = None, *, dry_run: bool = False) -> dict:
    settings = smtp_settings(cfg)
    subject = "TV-Guide: Testmail"
    text = "Dies ist eine Testmail vom TV-Guide."
    html = "<p>Dies ist eine <b>Testmail</b> vom TV-Guide.</p>"
    if dry_run:
        return {"test": True, "dry_run": True, "file": _write_eml(cfg, settings, subject, text, html)}
    if notifier is None:
        if not settings["host"]:
            return {"test": True, "sent": 0, "skipped": "SMTP nicht konfiguriert"}
        notifier = EmailNotifier(settings)
    try:
        notifier.send(subject, text, html)
    except Exception as exc:  # noqa: BLE001
        return {"test": True, "sent": 0, "error": str(exc)}
    return {"test": True, "sent": 1}
