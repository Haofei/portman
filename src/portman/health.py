"""Setup health checks behind `portman doctor`. `run_checks(cfg)` returns a list
of (level, name, detail) so the command layer only formats; checks are
data-driven and easy to extend."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .db import DB
from .model import Status, Side

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
MARK = {PASS: "✓", WARN: "•", FAIL: "✗"}


def _adapters_load(cfg) -> None:
    from .adapters import get_adapter
    get_adapter(cfg.upstream.adapter, cfg.generic_adapters.get(cfg.upstream.adapter))
    get_adapter(cfg.target.adapter, cfg.generic_adapters.get(cfg.target.adapter))


def _curated_valid(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                json.loads(line)
        return True
    except Exception:
        return False


def run_checks(cfg) -> list[tuple[str, str, str]]:
    """Return [(level, name, detail)] for every setup check."""
    checks: list[tuple[str, str, str]] = []

    def add(ok, name, detail="", warn=False):
        checks.append((PASS if ok else (WARN if warn else FAIL), name, detail))

    add(cfg.upstream.root.is_dir(), "upstream root exists", str(cfg.upstream.root))
    add(cfg.target.root.is_dir(), "target root exists", str(cfg.target.root))
    git_ok = subprocess.run(["git", "-C", str(cfg.upstream.root), "rev-parse", "--show-toplevel"],
                            capture_output=True).returncode == 0
    add(git_ok, "upstream is a git repo (for snapshot)", "", warn=not git_ok)
    try:
        _adapters_load(cfg)
        add(True, "adapters loadable", f"{cfg.upstream.adapter}, {cfg.target.adapter}")
    except Exception as e:
        add(False, "adapters loadable", str(e))
    try:
        cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        add(True, "reports dir writable", str(cfg.reports_dir))
    except Exception as e:
        add(False, "reports dir writable", str(e))
    cur = cfg.root / "mappings" / "curated.jsonl"
    add(_curated_valid(cur), "curated.jsonl parses", str(cur))

    if not cfg.db_path.exists():
        add(True, "database present", "(not built yet — run `portman inventory`)", warn=True)
        return checks

    db = DB(cfg.db_path)
    pe = (db.parse_errors(Side.UPSTREAM.value, cfg.upstream.version)
          + db.parse_errors(Side.TARGET.value, cfg.target.version))
    add(not pe, f"no parse errors ({len(pe)})", "; ".join(r["path"] for r in pe[:3]), warn=bool(pe))
    dups = db.duplicate_targets()
    add(not dups, f"no duplicate target mappings ({len(dups)})",
        "run `portman map` after fixes", warn=bool(dups))
    bad_dev = [r for r in db.mappings()
               if r["status"] == Status.DIVERGED.value and not r["deviation_id"]]
    add(not bad_dev, f"diverged mappings have deviation ids ({len(bad_dev)})")
    # alias integrity: each aliased mapping must name a primary that shares its target
    by_sid = db.mapping_index()
    bad_alias = [r["upstream_sid"] for r in by_sid.values()
                 if r["status"] == Status.ALIASED.value
                 and (not r["covers"] or not by_sid.get(r["covers"])
                      or by_sid[r["covers"]]["target_sid"] != r["target_sid"])]
    add(not bad_alias, f"aliased mappings reference a valid primary ({len(bad_alias)})",
        "; ".join(bad_alias[:3]))
    return checks
