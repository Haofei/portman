"""Proves portman's matcher is language-agnostic: the Python/rsscript naming
conventions are OPT-IN config, not baked into the core. With default (generic)
rules the language-specific tricks are OFF; turning on config flags enables them.

Run: PYTHONPATH=src python3 tests/agnostic.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from portman.matching import MappingRules, match_score
from portman.adapters.base import Adapter
from portman.adapters.rss import RssAdapter
from portman.adapters.python_ast import PythonAdapter


def sym(path, qual, kind, sig=""):
    return {"path": path, "qualname": qual, "kind": kind, "signature": sig, "is_public": True}


def main() -> int:
    f = []

    generic = MappingRules()                                   # defaults: conventions OFF
    py_rss = MappingRules(dunder_passthrough=True, inplace_suffix="_inplace")

    u_inplace = sym("t.py", "Tensor.to_", "method")
    t_inplace = sym("t.rss", "tensor_to_inplace", "function")
    u_dunder = sym("t.py", "Tensor.__hash__", "method")
    t_dunder = sym("t.rss", "__hash__", "function")

    # generic: language tricks OFF -> no in-place / dunder exact match
    if match_score(u_inplace, t_inplace, generic) == 4:
        f.append("generic rules wrongly applied in-place convention")
    if match_score(u_dunder, t_dunder, generic) == 4:
        f.append("generic rules wrongly applied dunder passthrough")

    # opt-in: with the Python/rss flags, both match exactly
    if match_score(u_inplace, t_inplace, py_rss) != 4:
        f.append("in-place convention not applied when configured")
    if match_score(u_dunder, t_dunder, py_rss) != 4:
        f.append("dunder passthrough not applied when configured")

    # universal behaviour holds regardless: a method matches a flattened owner_name
    u_m = sym("t.py", "Tensor.reshape", "method")
    t_m = sym("t.rss", "tensor_reshape", "function")
    if match_score(u_m, t_m, generic) < 3:
        f.append("owner-qualified (universal) match broken under generic rules")

    # signature parsing lives in adapters, not the core
    if Adapter.arg_types(Adapter, "(x: int)") != []:
        f.append("base adapter should parse no arg types")
    if RssAdapter().arg_types("(d: read DType, id: Int)") != [("d", "DType"), ("id", "Int")]:
        f.append("rss adapter arg_types parse failed")
    # python adapter has no language-specific arg parser leaking in (default none)
    if PythonAdapter().arg_types("(x: int)") != []:
        f.append("python adapter unexpectedly parses args")

    if f:
        print("AGNOSTIC FAIL:")
        for x in f: print("  -", x)
        return 1
    print("AGNOSTIC OK: language conventions are opt-in config; signature parsing is in adapters")
    return 0


def test_agnostic():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
