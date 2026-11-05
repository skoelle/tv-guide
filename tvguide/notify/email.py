"""E-Mail-Versand per smtplib (nur Standardbibliothek)."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from ..config import Config
from . import Notifier


def smtp_settings(cfg: Config) -> dict:
    """SMTP-Einstellungen aus config.toml; das Passwort kommt aus der Umgebungsvariable."""
    s = dict(cfg.smtp or {})
    to = s.get("to") or []
    if isinstance(to, str):
        to = [to]
    return {
        "host": str(s.get("host", "") or ""),
        "port": int(s.get("port", 587)),
        "security": str(s.get("security", "starttls") or "starttls").lower(),
        "username": str(s.get("username", "") or ""),
        "password": os.environ.get("TVGUIDE_SMTP_PASSWORD", ""),
        "from_addr": str(s.get("from", "") or ""),
        "to": [str(x) for x in to],
    }


def build_message(settings: dict, subject: str, text: str, html: str) -> EmailMessage:
    """Baut eine multipart/alternative-Nachricht mit Text- und HTML-Teil."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.get("from_addr") or settings.get("username") or "tvguide@localhost"
    msg["To"] = ", ".join(settings.get("to") or []) or "unset@localhost"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


class EmailNotifier(Notifier):
    def __init__(self, settings: dict):
        self.settings = settings

    def send(self, subject: str, text: str, html: str) -> None:
        msg = build_message(self.settings, subject, text, html)
        host, port = self.settings["host"], self.settings["port"]
        if self.settings["security"] == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        try:
            if self.settings["security"] == "starttls":
                server.starttls()
            if self.settings["username"]:
                server.login(self.settings["username"], self.settings["password"])
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass
