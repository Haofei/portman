"""Dependency-free smoke test. Run: PYTHONPATH=src python3 tests/smoke.py
Exercises the full pipeline on a tiny synthetic upstream/target pair so CI can
gate the framework itself without needing the real repos."""
from __future__ import annotations

from harness import synthetic_port
from portman import inventory, progress, diff as diffmod

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
    with synthetic_port({"dtype.py": UP_DTYPE}, {"dtype.rss": TG_DTYPE},
                        run_inventory=False) as (cfg, db):
        up_dir = cfg.upstream.root
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
        import textwrap
        from portman.adapters import get_adapter
        (up_dir / "dtype.py").write_text(textwrap.dedent(UP_DTYPE).replace(
            "def least_upper_dtype(a, b)", "def least_upper_dtype(a, b, c)"))
        syms = get_adapter("python").extract_tree(up_dir, "upstream", "up", "v2")
        db.replace_symbols("upstream", "v2", syms)
        rep = diffmod.upgrade_report(db, "v1", "v2")
        assert rep["summary"]["signature_changed"] == 1, rep["summary"]

        # version-alias round-trip: a tag resolves to its stored sha (snapshot/diff)
        db.set_version_alias("v2.0.0", "deadbeef")
        assert db.resolve_version("v2.0.0") == "deadbeef"
        assert db.resolve_version("unknown-ref") == "unknown-ref"
        assert db.has_version("upstream", "v2") and not db.has_version("upstream", "nope")

        # `set` must NOT expose `aliased` (it can't supply `covers`) — use `alias`.
        from portman.cli import build_parser
        import contextlib, io
        with contextlib.suppress(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            build_parser().parse_args(["set", "aliased", "--upstream", "x::Y"])
            raise AssertionError("`set aliased` should be rejected by argparse choices")

        print("SMOKE OK:", {"linked": res["linked"], "weighted": cov["weighted_pct"],
                            "sig_changes": rep["summary"]["signature_changed"]})
    return 0


def test_smoke():               # pytest entry point
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
