"""Shared command helpers: config load + DB open used by nearly every command."""
from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..db import DB


def _cfg(args) -> Config:
    return Config.load(Path(args.config))


def _db(cfg: Config) -> DB:
    db = DB(cfg.db_path)
    db.import_curated(cfg.root / "mappings" / "curated.jsonl")
    return db


def _ctx(args) -> tuple[Config, DB]:
    """Load config + open the DB (with curated facts imported) — the common
    preamble for nearly every command."""
    cfg = _cfg(args)
    return cfg, _db(cfg)
