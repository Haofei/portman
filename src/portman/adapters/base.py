"""Adapter contract. One adapter per language/runtime. An adapter turns a source
tree into a flat list of `Symbol`s. Add a new target language by writing one
adapter and registering it in `__init__.py`; nothing else in the framework needs
to know the language."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

from ..model import Symbol


def h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:16]


class Adapter(ABC):
    #: file globs this adapter claims, e.g. ("*.py",)
    patterns: tuple[str, ...] = ()
    name: str = "base"

    @abstractmethod
    def extract_file(self, root: Path, file: Path, side: str, repo: str,
                     version: str) -> list[Symbol]:
        """Return all symbols in one file, including a FILE-kind symbol for the
        file itself."""

    def discover(self, root: Path) -> list[Path]:
        out: list[Path] = []
        for pat in self.patterns:
            out.extend(p for p in root.rglob(pat) if p.is_file())
        return sorted(set(out))

    def extract_tree(self, root: Path, side: str, repo: str,
                     version: str, exclude: tuple[str, ...] = ()) -> list[Symbol]:
        syms: list[Symbol] = []
        for f in self.discover(root):
            rel = f.relative_to(root).as_posix()
            if any(part in rel for part in exclude):
                continue
            try:
                syms.extend(self.extract_file(root, f, side, repo, version))
            except Exception as e:  # never let one bad file kill the inventory
                syms.append(Symbol(side=side, repo=repo, path=rel, qualname="",
                                   kind="file", note_error=str(e)) if False else
                            Symbol(side=side, repo=repo, path=rel, qualname="",
                                   kind="file", signature=f"PARSE_ERROR: {e}",
                                   version=version))
        return syms
