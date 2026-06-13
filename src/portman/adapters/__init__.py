"""Adapter registry. Map an adapter name (used in portman.toml) to a factory."""
from __future__ import annotations

from .base import Adapter
from .python_ast import PythonAdapter
from .rss import RssAdapter
from .generic import GenericRegexAdapter

BUILTINS = {
    "python": PythonAdapter,
    "rss": RssAdapter,
}


def get_adapter(name: str, generic_cfg: dict | None = None) -> Adapter:
    if name in BUILTINS:
        return BUILTINS[name]()
    if generic_cfg:
        return GenericRegexAdapter(name, tuple(generic_cfg["patterns"]),
                                   generic_cfg["rules"])
    raise KeyError(f"unknown adapter {name!r}; register it in adapters/__init__.py "
                   f"or define [adapters.{name}] in portman.toml")
