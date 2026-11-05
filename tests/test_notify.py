from pathlib import Path

from tvguide.db import connect
from tvguide.matching import run_match
from tvguide.notify.digest import pending_hits, render_digest, send_notifications, send_test
from tvguide.notify.email import build_message, smtp_settings
from tvguide.review import apply_override
from tvguide.search import create_search, run_searches


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, subject, text, html):
        self.sent.append((subject, text, html))


class BoomNotifier:
    def send(self, subject, text, html):
        raise RuntimeError("SMTP kaputt")


def _setup(cfg, notify=True):
    con = connect(cfg)
    run_match(con, cfg)
    apply_override(con, "sendung 0", "tt0086659")
    create_search(con, cfg, "Alles", {}, notify=notify)
    run_searches(con, cfg)
    return con


def test_smtp_settings_aus_env(cfg, monkeypatch):
    monkeypatch.setenv("TVGUIDE_SMTP_PASSWORD", "geheim")
    s = smtp_settings(cfg)
    assert s["password"] == "geheim" and s["security"] == "starttls"
    assert s["host"] == "" and s["to"] == []


def test_build_message_alternative(cfg):
    settings = smtp_settings(cfg)
    settings["to"] = ["a@example.org"]
    settings["from_addr"] = "tv@example.org"
    msg = build_message(settings, "Betreff", "Text", "<p>HTML</p>")
    assert msg["Subject"] == "Betreff"
    assert msg["To"] == "a@example.org"
    assert msg.is_multipart()
    types = {p.get_content_type() for p in msg.iter_parts()}
    assert types == {"text/plain", "text/html"}


def test_render_digest(cfg):
    con = _setup(cfg)
    hits = pending_hits(con)
    text, html = render_digest(hits, cfg)
    con.close()
    assert "Alles (1)" in text
    assert "Sendung 0" in text
    assert "/work/tt0086659" in text and "/work/tt0086659" in html
    assert "<ul>" in html


def test_send_setzt_notified_at(cfg):
    con = _setup(cfg)
    fake = FakeNotifier()
    res = send_notifications(con, cfg, fake)
    assert res == {"pending": 1, "sent": 1}
    assert fake.sent and "1 neue Treffer" in fake.sent[0][0]
    assert pending_hits(con) == []              # notified_at gesetzt
    assert send_notifications(con, cfg, fake)["sent"] == 0
    con.close()


def test_fehler_laesst_notified_at_leer(cfg):
    con = _setup(cfg)
    res = send_notifications(con, cfg, BoomNotifier())
    assert res["sent"] == 0 and "SMTP kaputt" in res["error"]
    assert len(pending_hits(con)) == 1          # bleibt offen fuer den naechsten Lauf
    con.close()


def test_dry_run_schreibt_eml(cfg):
    con = _setup(cfg)
    res = send_notifications(con, cfg, dry_run=True)
    con.close()
    assert res["dry_run"] and res["pending"] == 1
    assert Path(res["file"]).exists()
    assert Path(res["file"]).read_text(encoding="utf-8", errors="replace").startswith("Subject:") or \
        "Subject:" in Path(res["file"]).read_text(encoding="utf-8", errors="replace")


def test_testmail_dry_run(cfg):
    res = send_test(cfg, dry_run=True)
    assert res["test"] and Path(res["file"]).exists()
