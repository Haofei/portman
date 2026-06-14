"""Shared test scaffolding: build a throwaway upstream(Python)/target(rss) port in
a temp dir, run inventory (+map), and hand back (cfg, db). Removes the tempdir +
portman.toml + Config/DB/build_inventory boilerplate every test was repeating."""
from __future__ import annotations

import sys
import tempfile
import textwrap
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from portman.config import Config        # noqa: E402
from portman.db import DB                # noqa: E402
from portman import inventory            # noqa: E402


def _cfg_text(cfg_extra: str, target_inventory: str) -> str:
    inv = f'inventory = "{target_inventory}"\n' if target_inventory else ""
    return (
        'project = "test"\n'
        'db = "port.db"\n'
        'reports = "reports"\n'
        '[upstream]\n'
        'repo = "up"\nroot = "up"\nadapter = "python"\nversion = "v1"\n'
        '[target]\n'
        'repo = "tg"\nroot = "tg/src"\nadapter = "rss"\nversion = "working"\n'
        f'{inv}{cfg_extra}'
    )


def _write(base: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))


@contextmanager
def synthetic_port(up_files: dict[str, str], tg_files: dict[str, str] | None = None,
                   cfg_extra: str = "", target_inventory: str = "",
                   extra_files: dict[str, str] | None = None,
                   run_inventory: bool = True, run_map: bool = True):
    """Yield (cfg, db) for a temp port. up_files/tg_files map a repo-relative path
    to source text (dedented). cfg_extra appends TOML sections (e.g. [mapping]).
    target_inventory sets [target] inventory=...; extra_files are written at the
    project root (e.g. the inventory JSON). Set run_inventory=False to drive
    build_inventory/auto_map yourself (e.g. to assert on their return values)."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write(root / "up", up_files)
        _write(root / "tg/src", tg_files or {})
        _write(root, extra_files or {})
        (root / "portman.toml").write_text(_cfg_text(cfg_extra, target_inventory))
        cfg = Config.load(root / "portman.toml")
        db = DB(cfg.db_path)
        if run_inventory:
            inventory.build_inventory(cfg, db)
            if run_map:
                inventory.auto_map(cfg, db)
        yield cfg, db
