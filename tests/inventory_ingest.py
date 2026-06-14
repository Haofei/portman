"""Test the compiler-inventory ingestion adapter (#4): a JSON inventory with
SOURCE-level names matches upstream exactly (no name-bridging needed), and the
scraper remains the fallback when no inventory file is present.

Run: PYTHONPATH=src python3 tests/inventory_ingest.py
"""
from __future__ import annotations

import json

from harness import synthetic_port
from portman.config import Config
from portman.db import DB
from portman import inventory

# upstream modules whose lowered RSS names differ from source
UP_FILES = {"helpers.py": "def count(x): ...\n",
            "tensor.py": "class Tensor:\n    def reshape(self, *s): ...\n"}

# A compiler-produced inventory using SOURCE names (count, Tensor.reshape) plus
# the lowered names as metadata. matches upstream exactly.
INV = {"symbols": [
    {"module": "helpers", "qualname": "count", "kind": "function",
     "visibility": "public", "source_span": [1, 1], "lowered_name": "helpers_count"},
    {"module": "tensor", "qualname": "Tensor", "kind": "class", "visibility": "public",
     "source_span": [2, 4]},
    {"module": "tensor", "qualname": "Tensor.reshape", "kind": "method",
     "visibility": "public", "source_span": [3, 3], "lowered_name": "tensor_reshape"},
]}


def main() -> int:
    f = []
    with synthetic_port(UP_FILES, target_inventory="inv.json",
                        extra_files={"inv.json": json.dumps(INV)},
                        run_inventory=False) as (cfg, db):
        res = inventory.build_inventory(cfg, db)
        if res.get("target_source") != "inventory":
            f.append(f"did not use inventory adapter: {res.get('target_source')}")
        # target symbols carry SOURCE qualnames + lowered metadata
        tnames = {s["qualname"]: s for s in db.symbols("target", "working")}
        if "count" not in tnames:
            f.append(f"source name 'count' missing from target: {list(tnames)}")
        if "lowered=helpers_count" not in (tnames.get("count", {})["signature"] if "count" in tnames else ""):
            f.append("lowered_name not preserved")

        inventory.auto_map(cfg, db)

        def status_of(path, qual, kind):
            from portman.model import symbol_id
            sid = symbol_id("up", path, qual, kind)
            m = db.mapping(sid)
            return m["status"] if m else None

        # exact source-name match => implemented without any name-bridging config
        if status_of("helpers.py", "count", "function") != "implemented":
            f.append("count not matched via inventory source name")
        if status_of("tensor.py", "Tensor.reshape", "method") != "implemented":
            f.append("Tensor.reshape not matched via inventory source name")

        # fallback: remove the inventory file -> scraper is used
        cfg.target.inventory.unlink()
        cfg2 = Config.load(cfg.root / "portman.toml")
        res2 = inventory.build_inventory(cfg2, DB(cfg.root / "port2.db"))
        if res2.get("target_source") != "scraper":
            f.append(f"fallback to scraper failed: {res2.get('target_source')}")

    if f:
        print("INGEST FAIL:")
        for x in f: print("  -", x)
        return 1
    print("INGEST OK: inventory source-names match exactly; scraper fallback works")
    return 0


def test_inventory_ingest():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
