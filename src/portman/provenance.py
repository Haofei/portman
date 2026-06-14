"""Provenance extraction from target source files.

Two formats are supported:

1. Canonical structured header (recommended, machine-first):

       // @port upstream: tinygrad/dtype.py
       // @port symbols: DType, PtrDType, least_upper_dtype
       // @port version: fa400f9790ab9a684387b02e958658217b33e7c1
       // @port status: implemented
       // @port deviation: D-0007

2. Legacy free-form line already used in the codebase:

       // 1:1 port of tinygrad/uop/ops.py — the UOp IR node ...
       // 1:1-oriented port of tinygrad/dtype.py.

The legacy form yields the upstream path only; the canonical form yields the
full provenance tuple. `portman provenance lint` flags files that still use the
legacy form so they can be upgraded incrementally."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CANON = re.compile(r"@port\s+(\w+)\s*[:=]\s*(.+?)\s*$", re.MULTILINE)


def _legacy_re(exts: tuple[str, ...]) -> re.Pattern:
    """Legacy / free-form header: any mention of an upstream source path with one
    of the upstream file extensions, e.g. "1:1 port of tinygrad/uop/ops.py". The
    extension set comes from the upstream adapter, so this is language-agnostic."""
    alt = "|".join(re.escape(e.lstrip(".")) for e in exts) or "py"
    return re.compile(rf"\b((?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.(?:{alt}))\b")


DEFAULT_EXTS = ("py",)


@dataclass
class Provenance:
    target_path: str
    upstream_path: str = ""
    upstream_version: str = ""
    symbols: list[str] = field(default_factory=list)
    status: str = ""
    deviation: str = ""
    format: str = "none"          # canonical | legacy | none

    @property
    def declared(self) -> bool:
        return self.format != "none"


def parse(text: str, target_path: str, header_lines: int = 40,
          upstream_exts: tuple[str, ...] = DEFAULT_EXTS) -> Provenance:
    head = "\n".join(text.splitlines()[:header_lines])
    fields = {k.lower(): v for k, v in CANON.findall(head)}
    if fields:
        return Provenance(
            target_path=target_path,
            upstream_path=fields.get("upstream", ""),
            upstream_version=fields.get("version", ""),
            symbols=[s.strip() for s in re.split(r"[,\s]+", fields.get("symbols", "")) if s.strip()],
            status=fields.get("status", ""),
            deviation=fields.get("deviation", ""),
            format="canonical")
    m = _legacy_re(upstream_exts).search(head)
    if m:
        return Provenance(target_path=target_path, upstream_path=m.group(1),
                          format="legacy")
    return Provenance(target_path=target_path, format="none")


def parse_file(path: Path, root: Path, upstream_exts: tuple[str, ...] = DEFAULT_EXTS) -> Provenance:
    return parse(path.read_text(encoding="utf-8", errors="ignore"),
                 path.relative_to(root).as_posix(), upstream_exts=upstream_exts)
