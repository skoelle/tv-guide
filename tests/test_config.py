from pathlib import Path

from tvguide.config import load_config


def test_defaults_without_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TVGUIDE_CONFIG", raising=False)
    cfg = load_config()
    assert cfg.source is None
    assert cfg.port == 8085
    assert cfg.epg_db == (tmp_path / "data" / "epg.db").resolve()


def test_relative_paths_follow_config_file(tmp_path):
    sub = tmp_path / "proj"
    sub.mkdir()
    f = sub / "config.toml"
    f.write_text('''
[paths]
epg_db = "daten/epg.db"
app_db = "/tmp/abs/app.db"
[server]
port = 9090
[matching]
auto_threshold = 1.2
[channel_groups]
retro = ["TELE 5", "ONE"]
''')
    cfg = load_config(f)
    assert cfg.epg_db == (sub / "daten" / "epg.db").resolve()
    assert cfg.app_db == Path("/tmp/abs/app.db")
    assert cfg.port == 9090
    assert cfg.auto_threshold == 1.2
    assert cfg.channel_groups == {"retro": ["TELE 5", "ONE"]}


def test_missing_explicit_file(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "gibt-es-nicht.toml")
