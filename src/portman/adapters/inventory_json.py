"""Compiler-produced inventory adapter (#4).

Instead of scraping target source text with regexes (which loses checked symbol
identity, visibility, and source-vs-lowered names), ingest a JSON inventory the
target compiler can emit. This is preferred over the scraper when available; the
scraper stays the fallback.

Expected JSON: an object {"symbols": [...]} or a bare list of records. Each record:

    {
      "module":      "helpers",                # logical module / file path
      "qualname":    "count",                  # SOURCE-level name (not lowered)
      "kind":        "function",               # function|method|class|type|constant|test
      "visibility":  "public",                 # public|internal  (optional)
      "source_span": [12, 40],                 # [start_line, end_line] (optional)
      "lowered_name":"helpers_count"           # generated/compiled name (optional)
    }

Using the SOURCE-level `qualname` means matching against upstream is exact and the
cross-language name-bridging heuristics are no longer load-bearing. `lowered_name`
is kept (in the signature field) for traceability."""
from __future__ import annotations

import json
from pathlib import Path

from ..model import Symbol, SymbolKind
from .base import Adapter, h, _excluded


class JsonInventoryAdapter(Adapter):
    name = "inventory"

    def __init__(self, json_path: Path):
        super().__init__()
        self.json_path = Path(json_path)

    def discover(self, root: Path):
        return [self.json_path]

    def extract_file(self, root, file, side, repo, version):   # unused (whole-file JSON)
        return []

    def extract_tree(self, root, side, repo, version, exclude=(),
                     allow_parse_errors: bool = True):
        self.parse_errors = []
        try:
            data = json.loads(self.json_path.read_text())
        except Exception as e:
            if not allow_parse_errors:
                raise RuntimeError(f"bad inventory JSON {self.json_path}: {e}") from e
            self.parse_errors.append({"path": str(self.json_path), "error": str(e)})
            return []
        records = data["symbols"] if isinstance(data, dict) else data

        syms: list[Symbol] = []
        seen_modules: set[str] = set()
        for r in records:
            module = r["module"]
            if _excluded(module, exclude):
                continue
            if module not in seen_modules:
                seen_modules.add(module)
                syms.append(Symbol(side=side, repo=repo, path=module, qualname="",
                                   kind=SymbolKind.FILE.value, version=version,
                                   body_hash=h(module)))
            span = r.get("source_span") or [0, 0]
            sig = f"lowered={r['lowered_name']}" if r.get("lowered_name") else ""
            sym = Symbol(side=side, repo=repo, path=module, qualname=r["qualname"],
                         kind=r.get("kind", "function"), signature=sig,
                         lineno=span[0] if span else 0,
                         end_lineno=span[1] if len(span) > 1 else (span[0] if span else 0),
                         version=version, sig_hash=h(sig))
            if "visibility" in r:            # explicit visibility overrides the underscore heuristic
                sym.is_public = (r["visibility"] == "public")
            syms.append(sym)
        return syms
