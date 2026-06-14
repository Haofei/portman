"""Dependency-free architecture guard (import-linter-lite): enforces the module
layering so the structure can't silently regress (e.g. the matcher importing
orchestration). Runs in `make test`; a real import-linter contract lives in
pyproject.toml for when the tool is installed.

Run: PYTHONPATH=src python3 tests/layering.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "portman"

# A module (key) must NOT import any portman module in its forbidden set (value).
FORBIDDEN = {
    # the language-agnostic core must not depend on orchestration/analysis/IO
    "matching": {"inventory", "progress", "report", "commands", "cli", "classify",
                 "diff", "health", "adapters"},
    # foundation layers stay leaf-level
    "model": {"config", "db", "matching", "inventory", "progress", "report",
              "commands", "cli", "classify", "diff", "health", "provenance", "adapters"},
    "config": {"db", "matching", "inventory", "progress", "report", "commands",
               "cli", "classify", "diff", "health"},
    "db": {"matching", "inventory", "progress", "report", "commands", "cli",
           "classify", "diff", "health", "config", "provenance"},
    "classify": {"inventory", "progress", "report", "commands", "cli", "diff", "health"},
    # orchestration must not depend on the read model / IO / interface
    "inventory": {"progress", "report", "commands", "cli", "health"},
    "progress": {"report", "commands", "cli", "health"},
    "report": {"commands", "cli"},
}


def _imported_modules(path: Path) -> set[str]:
    """Top-level portman submodule names imported by this file (any dot depth)."""
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod.startswith("portman."):
                out.add(mod.split(".")[1])
            elif node.level and "." not in mod:        # from .X import / from ..X import
                out.add(mod.split(".")[0])
        if isinstance(node, ast.ImportFrom) and node.level and not node.module:
            for a in node.names:                        # from . import X / from .. import X
                out.add(a.name)
    return out


def main() -> int:
    failures = []
    for mod, forbidden in FORBIDDEN.items():
        f = SRC / f"{mod}.py"
        if not f.exists():
            failures.append(f"{mod}: module missing"); continue
        bad = _imported_modules(f) & forbidden
        if bad:
            failures.append(f"{mod} imports forbidden {sorted(bad)}")
    if failures:
        print("LAYERING FAIL:")
        for x in failures:
            print("  -", x)
        return 1
    print(f"LAYERING OK: {len(FORBIDDEN)} module contracts hold")
    return 0


def test_layering():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
