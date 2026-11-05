"""Benachrichtigungen. austauschbare Schnittstelle mit E-Mail als erster Umsetzung."""
from __future__ import annotations


class Notifier:
    """Schnittstelle fuer Benachrichtigungskanaele."""

    def send(self, subject: str, text: str, html: str) -> None:  # pragma: no cover - Interface
        raise NotImplementedError


class NullNotifier(Notifier):
    """Verwirft Nachrichten (SMTP nicht konfiguriert)."""

    def send(self, subject: str, text: str, html: str) -> None:
        return None
