"""Adapter contract. One adapter per language/runtime. An adapter turns a source
tree into a flat list of `Symbol`s. Add a new target language by writing one
adapter and registering it in `__init__.py`; nothing else in the framework needs
to know the language."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from fnmatch import fnmatch
from pathlib import Path

from ..model import Symbol


def h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:16]


def _excluded(rel: str, patterns: tuple[str, ...]) -> bool:
    """Glob/path-segment matching so an exclude like 'test' cannot accidentally
    drop 'contest' or 'latest'. A bare token matches a whole path segment; a
    pattern with glob chars is matched against the full relative path and against
    'pattern/**' so a directory name excludes its subtree."""
    segs = rel.split("/")
    for pat in patterns:
        if any(ch in pat for ch in "*?[]"):
            if fnmatch(rel, pat) or fnmatch(rel, f"{pat.rstrip('/')}/**"):
                return True
        elif pat in segs or fnmatch(rel, f"{pat}/**"):
            return True
    return False


class Adapter(ABC):
    #: file globs this adapter claims, e.g. ("*.py",)
    patterns: tuple[str, ...] = ()
    name: str = "base"

    def __init__(self):
        #: populated by extract_tree: [{"path", "error"}], surfaced by inventory
        self.parse_errors: list[dict] = []

    @abstractmethod
    def extract_file(self, root: Path, file: Path, side: str, repo: str,
                     version: str) -> list[Symbol]:
        """Return all symbols in one file, including a FILE-kind symbol for the
        file itself."""

    def arg_types(self, signature: str) -> list[tuple[str, str]]:
        """Parse a signature into [(param_name, type)] — used by the matcher for
        receiver inference. Default: none. Override in language adapters that want
        it (see rss.py). Keeps signature syntax out of the generic core."""
        return []

    def discover(self, root: Path) -> list[Path]:
        out: list[Path] = []
        for pat in self.patterns:
            out.extend(p for p in root.rglob(pat) if p.is_file())
        return sorted(set(out))

    def extract_tree(self, root: Path, side: str, repo: str, version: str,
                     exclude: tuple[str, ...] = (),
                     allow_parse_errors: bool = True) -> list[Symbol]:
        """Extract every non-excluded file. Files that fail to parse are recorded
        in self.parse_errors and emitted as a FILE symbol flagged PARSE_ERROR so
        they are visibly NOT counted as healthy inventory. With
        allow_parse_errors=False a parse failure raises."""
        self.parse_errors = []
        syms: list[Symbol] = []
        for f in self.discover(root):
            rel = f.relative_to(root).as_posix()
            if _excluded(rel, exclude):
                continue
            try:
                syms.extend(self.extract_file(root, f, side, repo, version))
            except Exception as e:
                if not allow_parse_errors:
                    raise RuntimeError(f"parse error in {rel}: {e}") from e
                self.parse_errors.append({"path": rel, "error": f"{type(e).__name__}: {e}"})
                syms.append(Symbol(side=side, repo=repo, path=rel, qualname="",
                                   kind="parse_error",
                                   signature=f"PARSE_ERROR: {e}", version=version))
        return syms
