"""Regression test for rsscript `module` awareness in the target adapter.

rsscript module isolation namespaces every symbol in a file under its `module`
name, so a de-prefixed `fn scalar` in `module dtype` is the same symbol the flat
port spelled `dtype_scalar`. The adapter reconstructs that conventional flat name
for plain functions (so the matcher's owner-prefix / receiver rules keep working),
while leaving types, methods, and constants by their declared names.

Asserts:
  1. Adapter output: in `module dtype`, `fn scalar` -> qualname `dtype_scalar`,
     `fn DType.vec` stays `DType.vec`, `struct DType` stays `DType`,
     `const DTYPES_DICT` stays `DTYPES_DICT`.
  2. End-to-end: the de-prefixed `fn truncate` strong-matches upstream
     `DType.truncate` via an `owner_prefix_aliases` (module `dtype` -> `DType`).

Run: PYTHONPATH=src python3 tests/module_matching.py
"""
from __future__ import annotations

from harness import synthetic_port

CFG_EXTRA = '[mapping.owner_prefix_aliases]\nDType = ["dtype"]\n'

UP = '''
class DType:
    def truncate(self, x): ...
'''

TG_MODULE = '''// @port upstream: up/dtype.py
module dtype

struct DType {}
const DTYPES_DICT: Int = 18
fn scalar(d: read DType) -> DType { return d }
fn DType.vec(self: read DType, n: Int) -> DType { return self }
fn truncate(x: Int) -> Int { return x }
'''


def main() -> int:
    failures = []
    with synthetic_port({"dtype.py": UP}, {"dtype.rss": TG_MODULE},
                        cfg_extra=CFG_EXTRA) as (cfg, db):
        # 1. adapter output: which target qualnames exist?
        rows = db.c.execute(
            "SELECT qualname, kind FROM symbols WHERE side='target' AND path='dtype.rss'"
        ).fetchall()
        names = {r["qualname"]: r["kind"] for r in rows}
        expect = {
            "dtype_scalar": "function",   # fn scalar -> module-flattened
            "DType.vec": "method",        # already owner-qualified, untouched
            "DType": "type",              # struct, untouched
            "DTYPES_DICT": "constant",    # const, untouched
            "dtype_truncate": "function",
        }
        for q, k in expect.items():
            if names.get(q) != k:
                failures.append(f"adapter: expected {q!r} ({k}), got kind {names.get(q)!r}")
        if "scalar" in names or "truncate" in names:
            failures.append(f"adapter: plain fns should be module-prefixed, got bare: "
                            f"{[n for n in ('scalar','truncate') if n in names]}")

        # 2. end-to-end: DType.truncate maps to the de-prefixed function (strong).
        s = db.c.execute(
            "SELECT sid FROM symbols WHERE side='upstream' AND version='v1' "
            "AND path='dtype.py' AND qualname='DType.truncate'").fetchone()
        m = db.mapping(s["sid"])
        got = None
        if m and m["target_sid"]:
            got = db.c.execute("SELECT qualname FROM symbols WHERE sid=? AND side='target'",
                               (m["target_sid"],)).fetchone()["qualname"]
        if got != "dtype_truncate":
            failures.append(f"mapping: DType.truncate -> {got!r}, want 'dtype_truncate'")

    if failures:
        print("MODULE-MATCH FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("MODULE-MATCH OK: module-flattened fns reconstruct the flat name; "
          "types/methods/consts untouched; DType.truncate maps to dtype_truncate")
    return 0


def test_module_matching():     # pytest entry point
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
