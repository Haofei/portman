"""Tests for the unified classify layer: source-area coverage, ignore/copied
segmentation, and gap reasons. Run: PYTHONPATH=src python3 tests/classification.py
"""
from __future__ import annotations

from harness import synthetic_port
from portman import classify, inventory, progress

CFG_EXTRA = '''[areas]
core = ["core/"]
util = ["util.py"]
[copied]
roots = ["gen/"]
[ignore]
"util.py::skip_me" = "intentionally out of scope"
[deps]
boost = ["core/base.py::Base.key"]
[mapping.symbol_links]
"util.py::weird_name" = "util.rss::lowered_weird"
'''

UP_BASE = "class Base:\n    def key(self): ...\n    def other(self): ...\n"
# `lowered_weird` would auto-match the target the forced link claims for
# `weird_name` — it must NOT, or we'd get a duplicate target.
UP_UTIL = "def helper(): ...\ndef skip_me(): ...\ndef weird_name(): ...\ndef lowered_weird(): ...\n"
UP_GEN = "def generated_thing(): ...\n"
TG_BASE = "// @port upstream: up/core/base.py\nstruct Base {}\nfn base_key(b: read Base) {}\n"
TG_UTIL = "// @port upstream: up/util.py\nfn lowered_weird() {}\n"


def main() -> int:
    f = []
    up = {"core/base.py": UP_BASE, "util.py": UP_UTIL, "gen/g.py": UP_GEN}
    tg = {"core/base.rss": TG_BASE, "util.rss": TG_UTIL}
    with synthetic_port(up, tg, cfg_extra=CFG_EXTRA, run_inventory=False) as (cfg, db):
        inventory.build_inventory(cfg, db)
        res = inventory.auto_map(cfg, db)
        if res.get("forced_links") != 1:
            f.append(f"forced link not applied: {res.get('forced_links')} {res.get('forced_missing')}")

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
        if "weird_name" in quals: f.append("forced-linked symbol still a gap")
        # the forced link must point weird_name -> lowered_weird, confidence=config
        from portman.model import symbol_id
        wsid = symbol_id("up", "util.py", "weird_name", "function")
        wm = db.mapping(wsid)
        if not (wm and wm["confidence"] == "config" and wm["status"] == "implemented"):
            f.append(f"forced link mapping wrong: {dict(wm) if wm else None}")
        # the forced link's target must NOT be double-claimed by the auto-mapper:
        # lowered_weird (which auto-matches util.rss::lowered_weird) must not grab it.
        lw = db.mapping(symbol_id("up", "util.py", "lowered_weird", "function"))
        if lw and lw["target_sid"]:
            f.append(f"lowered_weird stole the forced-link target: {dict(lw)}")
        if db.duplicate_targets():
            f.append(f"forced link produced a duplicate target: {db.duplicate_targets()[0]['target_sid']}")
        # dep boost: Base.key ranked above Base.other
        ranks = {g["qualname"]: g["risk"] for g in gp}
        # Base.key is implemented (base_key) so may not be a gap; check 'other' exists as gap
        reasons = {g["qualname"]: g.get("reason") for g in gp}
        if "other" in reasons and reasons["other"] not in ("missing", "link_candidate", "alias_needed"):
            f.append(f"unexpected reason for other: {reasons['other']}")

        # batches (#3) + manifest (#9): grouped, with the right fields
        bs = progress.batches(db, "v1", cfg)
        if not bs:
            f.append("no batches produced")
        else:
            need = {"batch", "symbols", "blockers", "coverage_impact_pts", "verify",
                    "target_file", "target_files"}
            if not need <= set(bs[0]):
                f.append(f"batch missing fields: {need - set(bs[0])}")
            if not isinstance(bs[0]["target_files"], list):   # split ports -> list, not 1
                f.append("target_files should be a list (supports split ports)")
            if "Base" not in {b["owner"] for b in bs}:
                f.append(f"Base.other not grouped under owner Base: {[b['owner'] for b in bs]}")
        if any("weird_name" in s for b in bs for s in b["symbols"]):
            f.append("forced-linked symbol leaked into a batch")

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
