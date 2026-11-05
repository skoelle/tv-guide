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
    channel_groups: dict[str, list[str]] = field(default_factory=dict)
    smtp: dict = field(default_factory=dict)
    source: Path | None = None  # geladene Konfigurationsdatei, falls vorhanden


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
        channel_groups={k: list(v) for k, v in raw.get("channel_groups", {}).items()},
        smtp=dict(raw.get("smtp", {})),
        source=cfg_file,
    )
