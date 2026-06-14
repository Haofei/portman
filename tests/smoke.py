"""Dependency-free smoke test. Run: PYTHONPATH=src python3 tests/smoke.py
Exercises the full pipeline on a tiny synthetic upstream/target pair so CI can
gate the framework itself without needing the real repos."""
from __future__ import annotations

import sys, tempfile, textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from portman.config import Config
from portman.db import DB
from portman import inventory, progress, diff as diffmod


CFG = """
project = "smoke"
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
"""

UP_DTYPE = '''
class DType:
    def itemsize(self): ...
def least_upper_dtype(a, b): ...
MAX_BITS = 64
'''

TG_DTYPE = '''
// @port upstream: up/dtype.py
// @port version: v1
struct DType { bits: Int }
fn itemsize(self: DType) -> Int { return 1 }
fn least_upper_dtype(a: DType, b: DType) -> DType { return a }
'''


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "up").mkdir()
        (root / "tg/src").mkdir(parents=True)
        (root / "up/dtype.py").write_text(textwrap.dedent(UP_DTYPE))
        (root / "tg/src/dtype.rss").write_text(textwrap.dedent(TG_DTYPE))
        (root / "portman.toml").write_text(CFG)

        cfg = Config.load(root / "portman.toml")
        db = DB(cfg.db_path)
        inv = inventory.build_inventory(cfg, db)
        assert inv["upstream_symbols"] >= 4, inv
        res = inventory.auto_map(cfg, db)
        assert res["file_pairs"] == 1, res
        assert res["linked"] >= 2, res        # itemsize + least_upper_dtype

        cov = progress.coverage(db, "v1")
        assert cov["weighted_pct"] > 0, cov
        gaps = progress.gaps(db, "v1")
        # MAX_BITS constant has no target -> a gap
        assert any(g["qualname"] == "MAX_BITS" for g in gaps), gaps

        # upstream change detection: store a "v2" with a signature change
        from portman.adapters import get_adapter
        (root / "up/dtype.py").write_text(textwrap.dedent(UP_DTYPE).replace(
            "def least_upper_dtype(a, b)", "def least_upper_dtype(a, b, c)"))
        syms = get_adapter("python").extract_tree(root / "up", "upstream", "up", "v2")
        db.replace_symbols("upstream", "v2", syms)
        rep = diffmod.upgrade_report(db, "v1", "v2")
        assert rep["summary"]["signature_changed"] == 1, rep["summary"]
        print("SMOKE OK:", {"linked": res["linked"], "weighted": cov["weighted_pct"],
                            "sig_changes": rep["summary"]["signature_changed"]})
    return 0


def test_smoke():               # pytest entry point
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
