"""Generic regex adapter — a config-driven fallback so the framework supports
*any* target language without writing a Python class. Patterns come from
portman.toml under [adapters.<name>]. Use this for languages you do not have a
real parser for yet; replace with a precise adapter later."""
from __future__ import annotations

import re

from ..model import Symbol, SymbolKind
from .base import Adapter, h


class GenericRegexAdapter(Adapter):
    def __init__(self, name: str, patterns: tuple[str, ...], rules: dict[str, str]):
        self.name = name
        self.patterns = patterns
        # rules: kind -> regex with one capture group for the symbol name
        self.rules = {k: re.compile(v, re.MULTILINE) for k, v in rules.items()}

    def extract_file(self, root, file, side, repo, version):
        rel = file.relative_to(root).as_posix()
        src = file.read_text(encoding="utf-8", errors="ignore")
        out = [Symbol(side=side, repo=repo, path=rel, qualname="",
                      kind=SymbolKind.FILE.value, version=version, body_hash=h(src))]
        for kind, rx in self.rules.items():
            for m in rx.finditer(src):
                out.append(Symbol(side=side, repo=repo, path=rel, qualname=m.group(1),
                                  kind=kind, version=version,
                                  lineno=src.count("\n", 0, m.start()) + 1))
        return out
