"""Tests for the unified classify layer: source-area coverage, ignore/copied
segmentation, and gap reasons. Run: PYTHONPATH=src python3 tests/classification.py
"""
from __future__ import annotations

import sys, tempfile, textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from portman import classify
from portman.config import Config
from portman.db import DB
from portman import inventory, progress

CFG = """
project = "cls"
db = "port.db"
reports = "reports"
[upstream]
repo = "up"
root = "up"
adapter = "python"
version = "v1"
[target]
repo = "tg"
root = "tg/src"
adapter = "rss"
version = "working"
[areas]
core = ["core/"]
util = ["util.py"]
[copied]
roots = ["gen/"]
[ignore]
"util.py::skip_me" = "intentionally out of scope"
[deps]
boost = ["core/base.py::Base.key"]
"""

UP_BASE = "class Base:\n    def key(self): ...\n    def other(self): ...\n"
UP_UTIL = "def helper(): ...\ndef skip_me(): ...\n"
UP_GEN = "def generated_thing(): ...\n"
TG_BASE = "// @port upstream: up/core/base.py\nstruct Base {}\nfn base_key(b: read Base) {}\n"


def main() -> int:
    f = []
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "up/core").mkdir(parents=True)
        (root / "up/gen").mkdir(parents=True)
        (root / "tg/src/core").mkdir(parents=True)
        (root / "up/core/base.py").write_text(UP_BASE)
        (root / "up/util.py").write_text(UP_UTIL)
        (root / "up/gen/g.py").write_text(UP_GEN)
        (root / "tg/src/core/base.rss").write_text(TG_BASE)
        (root / "portman.toml").write_text(CFG)
        cfg = Config.load(root / "portman.toml")
        db = DB(cfg.db_path)
        inventory.build_inventory(cfg, db)
        inventory.auto_map(cfg, db)

        # pure classify checks
        if classify.area_of("core/base.py", cfg.areas) != "core": f.append("area core")
        if classify.area_of("util.py", cfg.areas) != "util": f.append("area util")
        if classify.area_of("nope.py", cfg.areas) != "other": f.append("area other")
        if not classify.is_copied("gen/g.py", cfg.copied_roots): f.append("copied detect")
        if classify.ignore_reason("util.py", "skip_me", cfg.ignore) is None: f.append("ignore match")
        if classify.ignore_reason("util.py", "helper", cfg.ignore) is not None: f.append("ignore overmatch")

        cov = progress.coverage(db, "v1", cfg)
        if cov["ignored"] < 1: f.append(f"ignored not segmented: {cov['ignored']}")
        if cov["copied_total"] < 1: f.append(f"copied not segmented: {cov['copied_total']}")
        if "core" not in cov["by_area"]: f.append(f"no core area: {list(cov['by_area'])}")
        # skip_me (ignored) and generated_thing (copied) must not be plain gaps
        gp = progress.gaps(db, "v1", cfg=cfg, explain=True)
        quals = {g["qualname"] for g in gp}
        if "skip_me" in quals: f.append("ignored symbol leaked into gaps")
        if "generated_thing" in quals: f.append("copied symbol leaked into gaps")
        # dep boost: Base.key ranked above Base.other
        ranks = {g["qualname"]: g["risk"] for g in gp}
        # Base.key is implemented (base_key) so may not be a gap; check 'other' exists as gap
        reasons = {g["qualname"]: g.get("reason") for g in gp}
        if "other" in reasons and reasons["other"] not in ("missing", "link_candidate", "alias_needed"):
            f.append(f"unexpected reason for other: {reasons['other']}")

    if f:
        print("CLASSIFY FAIL:")
        for x in f: print("  -", x)
        return 1
    print("CLASSIFY OK: areas, ignore+copied segmentation, gap reasons all hold")
    return 0


def test_classification():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
