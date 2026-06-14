"""Regression test for cross-language name matching, focused on the Python
method-naming conventions that naive normalization gets wrong:

  - trailing-underscore in-place methods   to_  -> tensor_to_inplace
  - leading-underscore "private" methods   _data must NOT steal tensor_data
  - dunder methods                         __hash__ must beat 'hash'
  - exact target qualname beats a normalized tie (score 4)

Run: PYTHONPATH=src python3 tests/name_matching.py
"""
from __future__ import annotations

import sys, tempfile, textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from portman.config import Config
from portman.db import DB
from portman import inventory

CFG = """
project = "nm"
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

UP_TENSOR = '''
class Tensor:
    def data(self): ...
    def _data(self): ...
    def to(self, x): ...
    def to_(self, x): ...
    def shard(self): ...
    def shard_(self): ...
    def hash(self): ...
    def __hash__(self): ...
'''

# Note: NO tensor__data target on purpose — _data has no implementation.
TG_TENSOR = '''
// @port upstream: up/tensor.py
struct Tensor {}
fn tensor_data(t: read Tensor) -> Int { return 0 }
fn tensor_to(t: read Tensor, x: Int) -> Tensor { return t }
fn tensor_to_inplace(t: mut Tensor, x: Int) {}
fn tensor_shard(t: read Tensor) -> Tensor { return t }
fn tensor_shard_inplace(t: mut Tensor) {}
fn tensor_hash(t: read Tensor) -> Int { return 0 }
fn __hash__(t: read Tensor) -> Int { return 0 }
'''

EXPECT = {
    "Tensor.data": "tensor_data",
    "Tensor.to": "tensor_to",
    "Tensor.to_": "tensor_to_inplace",
    "Tensor.shard": "tensor_shard",
    "Tensor.shard_": "tensor_shard_inplace",
    "Tensor.hash": "tensor_hash",
    "Tensor.__hash__": "__hash__",
}


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "up").mkdir()
        (root / "tg/src").mkdir(parents=True)
        (root / "up/tensor.py").write_text(textwrap.dedent(UP_TENSOR))
        (root / "tg/src/tensor.rss").write_text(textwrap.dedent(TG_TENSOR))
        (root / "portman.toml").write_text(CFG)

        cfg = Config.load(root / "portman.toml")
        db = DB(cfg.db_path)
        inventory.build_inventory(cfg, db)
        inventory.auto_map(cfg, db)

        def target_of(qual: str):
            s = db.c.execute(
                "SELECT sid FROM symbols WHERE side='upstream' AND version='v1' "
                "AND path='tensor.py' AND qualname=?", (qual,)).fetchone()
            m = db.mapping(s["sid"])
            if not m or not m["target_sid"]:
                return None, (m["confidence"] if m else None)
            t = db.c.execute("SELECT qualname FROM symbols WHERE sid=? AND side='target'",
                             (m["target_sid"],)).fetchone()
            return t["qualname"], m["confidence"]

        failures = []
        for qual, want in EXPECT.items():
            got, _ = target_of(qual)
            if got != want:
                failures.append(f"{qual}: expected {want!r}, got {got!r}")

        # _data must NOT have stolen tensor_data; it has no real target.
        got, conf = target_of("Tensor._data")
        if got is not None:
            failures.append(f"Tensor._data should be unlinked, got {got!r} ({conf})")

        # target uniqueness: no target claimed twice
        dups = db.duplicate_targets()
        if dups:
            failures.append(f"duplicate target mappings: {[dict(r) for r in dups]}")

        if failures:
            print("NAME-MATCH FAIL:")
            for f in failures:
                print("  -", f)
            return 1
        print(f"NAME-MATCH OK: {len(EXPECT)} conventions resolved, _data unlinked, no dup targets")
    return 0


def test_name_matching():       # pytest entry point
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
