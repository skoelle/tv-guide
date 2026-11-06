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


def test_tvheadend_options(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('''
[epg]
url = "http://tvh:9981/xmltv/channels"
user = "epg-user"

[tvheadend]
url = "http://tvh:9981/api/"
user = "dvr-user"
dvr_config = "Mein Profil"
''')
    cfg = load_config(f)
    assert cfg.tvh_url == "http://tvh:9981/api"
    assert cfg.tvh_user == "dvr-user"
    assert cfg.tvh_dvr_config == "Mein Profil"


def test_poster_paths_and_defaults(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('''
[posters]
language = "en-US"
image_size = "w185"
cache_dir = "bilder"
refresh_days = 90
''')
    cfg = load_config(f)
    assert cfg.poster_language == "en-US"
    assert cfg.poster_image_size == "w185"
    assert cfg.poster_cache_dir == (tmp_path / "bilder").resolve()
    assert cfg.posters_dir == cfg.poster_cache_dir
    assert cfg.poster_refresh_days == 90
