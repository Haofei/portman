"""Target adapter for rsscript (`.rss`) — the tinygrad-rsmc target language.

rsscript has no Python AST, so we parse declarations with anchored regexes:
`fn name(...)`, `struct Name`, `enum Name`, and `const NAME`. It also reads the
provenance header (see provenance.py) but that is handled separately so the
adapter stays a pure symbol extractor."""
from __future__ import annotations

import re

from ..model import Symbol, SymbolKind
from .base import Adapter, h

FN = re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\(([^)]*)\)([^\{\n]*)",
                re.MULTILINE)
STRUCT = re.compile(r"^\s*(?:pub\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
ENUM = re.compile(r"^\s*(?:pub\s+)?(?:enum|sum)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
CONST = re.compile(r"^\s*(?:pub\s+)?(?:const|let)\s+([A-Z][A-Z0-9_]*)\b", re.MULTILINE)


_QUAL = re.compile(r"^(read|mut|fresh)\s+")


def _lineno(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


class RssAdapter(Adapter):
    name = "rss"
    patterns = ("*.rss",)

    def arg_types(self, signature: str) -> list[tuple[str, str]]:
        """Parse an rsscript signature `(name: read Type, ...)` into [(name, type)],
        stripping ownership qualifiers and generics. The matcher uses this for
        receiver inference; this adapter is the ONLY place that knows rss syntax."""
        inner = signature.strip()
        if not inner.startswith("("):
            return []
        inner = inner[1:].split(")", 1)[0].strip()
        out: list[tuple[str, str]] = []
        for param in inner.split(","):
            if ":" not in param:
                continue
            nm, ty = param.split(":", 1)
            ty = _QUAL.sub("", ty.strip()).split("<", 1)[0].strip()
            out.append((nm.strip(), ty))
        return out

    def extract_file(self, root, file, side, repo, version):
        rel = file.relative_to(root).as_posix()
        src = file.read_text(encoding="utf-8", errors="ignore")
        out: list[Symbol] = [Symbol(side=side, repo=repo, path=rel, qualname="",
                                    kind=SymbolKind.FILE.value, version=version,
                                    body_hash=h(src))]
        for m in FN.finditer(src):
            name, args, ret = m.group(1), m.group(2), m.group(3)
            sig = f"({args.strip()}){ret.strip()}"
            kind = SymbolKind.METHOD.value if "." in name else SymbolKind.FUNCTION.value
            out.append(Symbol(side=side, repo=repo, path=rel, qualname=name,
                              kind=kind, signature=sig,
                              lineno=_lineno(src, m.start()), version=version,
                              sig_hash=h(re.sub(r"\s+", "", sig))))
        for rx, kind in ((STRUCT, SymbolKind.TYPE), (ENUM, SymbolKind.TYPE),
                         (CONST, SymbolKind.CONSTANT)):
            for m in rx.finditer(src):
                out.append(Symbol(side=side, repo=repo, path=rel,
                                  qualname=m.group(1), kind=kind.value,
                                  lineno=_lineno(src, m.start()), version=version))
        return out
