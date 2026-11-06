"""Konfiguration aus config.toml (Python 3.11+: tomllib)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    base_dir: Path
    epg_db: Path
    imdb_db: Path
    app_db: Path
    ignore_channels_file: Path
    host: str = "0.0.0.0"
    port: int = 8085
    base_url: str = ""
    timezone: str = "Europe/Berlin"
    auto_threshold: float = 1.0
    review_threshold: float = 0.6
    retro_from: int = 1980
    retro_to: int = 1999
    retro_end_max: int = 2009
    skip_categories: list[str] = field(default_factory=list)
    skip_titles: list[str] = field(default_factory=list)
    skip_min_duration: int = 8
    skip_block_min_duration: int = 100
    skip_block_min_events: int = 10
    # Datenquellen (Import/Build im Container)
    epg_url: str = "http://docker-host-nas.fritz.box:9981/xmltv/channels"
    epg_user: str = ""
    epg_prune_days: int = 0
    # TVHeadend-Aufnahmen (Serien-Timer) ueber die JSON-API
    tvh_url: str = ""
    tvh_user: str = ""
    tvh_dvr_config: str = ""
    imdb_raw_dir: Path | None = None
    imdb_types: str = "movie,tvMovie,tvSeries,tvMiniSeries"
    imdb_min_votes: int = 0
    imdb_min_year: int = 0
    imdb_with_episodes: bool = False
    # Zeitplan (im Container aktiv)
    schedule_enabled: bool = False
    schedule_epg_every_minutes: int = 60
    schedule_imdb_weekday: int = 0     # 0 = Montag
    schedule_imdb_time: str = "04:30"
    # Poster von TMDB (Bilder werden lokal gecacht)
    poster_enabled: bool = True
    poster_language: str = "de-DE"
    poster_image_size: str = "w342"
    poster_cache_dir: Path | None = None
    poster_refresh_days: int = 180
    poster_missing_retry_days: int = 30
    poster_error_retry_days: int = 1
    poster_request_delay: float = 0.3
    channel_groups: dict[str, list[str]] = field(default_factory=dict)
    smtp: dict = field(default_factory=dict)
    source: Path | None = None  # geladene Konfigurationsdatei, falls vorhanden

    @property
    def posters_dir(self) -> Path:
        return self.poster_cache_dir or (self.app_db.parent / "posters")


# Standard-Skip-Regeln (match_test.py v2.1), falls config.toml keine [skip]-Sektion hat.
DEFAULT_SKIP_CATEGORIES: tuple[str, ...] = (
    "News",
    "Leisure hobbies : Advertisement",
    "Sports",
    "Social / Political",
    "Education / Science",
    "Children's / Youth programs : Pre-school",
)
DEFAULT_SKIP_TITLES: tuple[str, ...] = (
    "teleshopping", "sendeschluss", "tagesschau", "heute", "tagesthemen", "wetter",
    "nachrichten", "programmhinweis", "sendepause", "werbung", "infokanal", "infomercial",
    "derzeit keine uebertragung",
)


def _resolve(base: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def find_config_file(explicit: str | os.PathLike | None = None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("TVGUIDE_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    candidate = Path.cwd() / "config.toml"
    return candidate.resolve() if candidate.exists() else None


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg_file = find_config_file(path)
    raw: dict = {}
    if cfg_file is not None:
        if not cfg_file.exists():
            raise FileNotFoundError(f"Konfigurationsdatei nicht gefunden: {cfg_file}")
        with open(cfg_file, "rb") as f:
            raw = tomllib.load(f)
        base = cfg_file.parent
    else:
        base = Path.cwd().resolve()

    paths = raw.get("paths", {})
    server = raw.get("server", {})
    matching = raw.get("matching", {})
    retro = raw.get("retro", {})
    skip = raw.get("skip", {})
    epg = raw.get("epg", {})
    imdb = raw.get("imdb", {})
    schedule = raw.get("schedule", {})
    posters = raw.get("posters", {})
    tvheadend = raw.get("tvheadend", {})

    return Config(
        base_dir=base,
        epg_db=_resolve(base, paths.get("epg_db", "data/epg.db")),
        imdb_db=_resolve(base, paths.get("imdb_db", "data/imdb.db")),
        app_db=_resolve(base, paths.get("app_db", "data/app.db")),
        ignore_channels_file=_resolve(base, paths.get("ignore_channels_file", "channels_ignore.txt")),
        host=server.get("host", "0.0.0.0"),
        port=int(server.get("port", 8085)),
        base_url=(server.get("base_url", "") or "").rstrip("/"),
        timezone=server.get("timezone", "Europe/Berlin"),
        auto_threshold=float(matching.get("auto_threshold", 1.0)),
        review_threshold=float(matching.get("review_threshold", 0.6)),
        retro_from=int(retro.get("from_year", 1980)),
        retro_to=int(retro.get("to_year", 1999)),
        retro_end_max=int(retro.get("end_max", 2009)),
        skip_categories=[str(c) for c in skip.get("categories", DEFAULT_SKIP_CATEGORIES)],
        skip_titles=[str(t) for t in skip.get("titles", DEFAULT_SKIP_TITLES)],
        skip_min_duration=int(skip.get("min_duration", 8)),
        skip_block_min_duration=int(skip.get("block_min_duration", 100)),
        skip_block_min_events=int(skip.get("block_min_events", 10)),
        epg_url=str(epg.get("url", "http://docker-host-nas.fritz.box:9981/xmltv/channels")),
        epg_user=str(epg.get("user", "") or ""),
        epg_prune_days=int(epg.get("prune_days", 0) or 0),
        tvh_url=str(tvheadend.get("url", "") or "").rstrip("/"),
        tvh_user=str(tvheadend.get("user", "") or ""),
        tvh_dvr_config=str(tvheadend.get("dvr_config", "") or ""),
        imdb_raw_dir=_resolve(base, imdb.get("raw_dir", "data/imdb_raw")),
        imdb_types=str(imdb.get("types", "movie,tvMovie,tvSeries,tvMiniSeries")),
        imdb_min_votes=int(imdb.get("min_votes", 0) or 0),
        imdb_min_year=int(imdb.get("min_year", 0) or 0),
        imdb_with_episodes=bool(imdb.get("with_episodes", False)),
        schedule_enabled=_env_bool("TVGUIDE_SCHEDULE_ENABLED", bool(schedule.get("enabled", False))),
        schedule_epg_every_minutes=int(schedule.get("epg_every_minutes", 60)),
        schedule_imdb_weekday=int(schedule.get("imdb_weekday", 0)),
        schedule_imdb_time=str(schedule.get("imdb_time", "04:30")),
        poster_enabled=bool(posters.get("enabled", True)),
        poster_language=str(posters.get("language", "de-DE")),
        poster_image_size=str(posters.get("image_size", "w342")),
        poster_cache_dir=_resolve(base, posters.get("cache_dir", "data/posters")),
        poster_refresh_days=int(posters.get("refresh_days", 180)),
        poster_missing_retry_days=int(posters.get("missing_retry_days", 30)),
        poster_error_retry_days=int(posters.get("error_retry_days", 1)),
        poster_request_delay=float(posters.get("request_delay", 0.3)),
        channel_groups={k: list(v) for k, v in raw.get("channel_groups", {}).items()},
        smtp=dict(raw.get("smtp", {})),
        source=cfg_file,
    )
