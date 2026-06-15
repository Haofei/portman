"""Regression test: an explicit `fn Owner.method` outranks the flat function it
wraps AND a normalized collision, so it links instead of going ambiguous.

Reproduces the tinygrad-rsmc `UPat.reduce` case: the target defines both a flat
`upat_reduce` (inferred as UPat.reduce via owner_prefix_aliases) and an explicit
`fn UPat.reduce`, while upstream also has `UPat.__reduce__` (shares the snake form
`u_pat_reduce`). The explicit method must win upstream `UPat.reduce`.

Run: PYTHONPATH=src python3 tests/explicit_method.py
"""
from __future__ import annotations

from harness import synthetic_port

CFG_EXTRA = ('[mapping]\ndunder_passthrough = true\n'
             '[mapping.owner_prefix_aliases]\nUPat = ["upat"]\n')

UP = '''
class UPat:
    def reduce(self, *src): ...
    def __reduce__(self): ...
'''

TG = '''// @port upstream: up/upat.py
struct UPat {}
fn upat_reduce(value: read UPat) -> UPat { return value }
fn UPat.reduce(self: read UPat) -> UPat { return self }
'''


def main() -> int:
    failures = []
    with synthetic_port({"upat.py": UP}, {"upat.rss": TG}, cfg_extra=CFG_EXTRA) as (cfg, db):
        s = db.c.execute(
            "SELECT sid FROM symbols WHERE side='upstream' AND version='v1' "
            "AND path='upat.py' AND qualname='UPat.reduce'").fetchone()
        m = db.mapping(s["sid"])
        got = None
        if m and m["target_sid"]:
            got = db.c.execute("SELECT qualname FROM symbols WHERE sid=? AND side='target'",
                               (m["target_sid"],)).fetchone()["qualname"]
        if got != "UPat.reduce":
            failures.append(f"UPat.reduce -> {got!r} ({m['confidence'] if m else None}), "
                            f"want explicit 'UPat.reduce'")
        if db.duplicate_targets():
            failures.append(f"duplicate target mappings: {[dict(r) for r in db.duplicate_targets()]}")

    if failures:
        print("EXPLICIT-METHOD FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("EXPLICIT-METHOD OK: explicit fn UPat.reduce wins over flat upat_reduce + __reduce__ collision")
    return 0


def test_explicit_method():     # pytest entry point
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
