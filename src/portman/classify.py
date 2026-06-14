"""Shared symbol classification: source area, ignore/copied segmentation, and the
unified gap-reason model. One place so coverage(), gaps(), report, and
`gaps --explain` all agree on what a symbol *is* and *why* it isn't ported.

A `gap_reason` answers "why isn't this a plain port task?":

  derived (portman figures it out):
    ignored            matches a [ignore] rule (out of scope, with a reason)
    copied_generated   under a [copied] root (copied/bound, not hand-ported)
    alias_needed       a name-collision (mapping confidence=ambiguous)
    kind_mismatch      a same-named target exists but its kind is incompatible
    already_mapped     a same-named target exists but is taken by another upstream
    link_candidate     a close target name exists in the file (needs a forced link)
    type_only          a type/constant with no target yet
    missing            no target candidate found
  declared (human, via [gap_reasons]):
    blocked_by_rss | blocked_by_mc | needs_review | <any custom>
"""
from __future__ import annotations

import fnmatch


def area_of(path: str, areas: dict[str, list[str]]) -> str:
    """First configured area whose any prefix matches the path; else 'other'."""
    for name, prefixes in areas.items():
        if any(path.startswith(p) or path == p for p in prefixes):
            return name
    return "other"


def is_copied(path: str, copied_roots) -> bool:
    return any(path.startswith(r) for r in copied_roots)


def _spec_matches(path: str, qualname: str, spec: str) -> bool:
    """Match a 'path', 'path::Qual', or 'path::*'/glob spec against a symbol."""
    s_path, sep, s_qual = spec.partition("::")
    if not fnmatch.fnmatch(path, s_path):
        return False
    if not sep:                       # path-only spec matches every symbol in it
        return True
    return fnmatch.fnmatch(qualname or "", s_qual)


def ignore_reason(path: str, qualname: str, ignore: dict[str, str]) -> str | None:
    for spec, reason in ignore.items():
        if _spec_matches(path, qualname, spec):
            return reason
    return None


def declared_reason(path: str, qualname: str, gap_reasons: dict[str, str]) -> str | None:
    for spec, reason in gap_reasons.items():
        if _spec_matches(path, qualname, spec):
            return reason
    return None


def gap_reason(sym, status: str, mapping, target_match: dict | None,
               cfg) -> tuple[str, str]:
    """Return (reason, detail). `target_match` is the best in-file target candidate
    found by the matcher (or None), with keys: qualname, kind, taken_by."""
    path, qual = sym["path"], sym["qualname"] or ""

    # structural reasons win first (they change the denominator, not just the label)
    r = ignore_reason(path, qual, cfg.ignore)
    if r is not None:
        return "ignored", r
    if is_copied(path, cfg.copied_roots):
        return "copied_generated", "under a configured copied/generated root"

    # explicit human declaration next
    r = declared_reason(path, qual, cfg.gap_reasons)
    if r is not None:
        return r, "declared in [gap_reasons]"

    # derived from mapping/match state
    if mapping and mapping["confidence"] == "ambiguous":
        return "alias_needed", mapping["note"] or "name-collision; needs alias/link"
    if target_match:
        if target_match.get("taken_by"):
            return "already_mapped", f"target `{target_match['qualname']}` already maps to {target_match['taken_by']}"
        if target_match.get("kind_mismatch"):
            return "kind_mismatch", f"target `{target_match['qualname']}` exists but kind {target_match['kind']} is incompatible"
        return "link_candidate", f"close target `{target_match['qualname']}` exists — add a forced symbol link"
    if sym["kind"] in ("type", "constant"):
        return "type_only", "type/constant with no target (may be type-only or need a link)"
    return "missing", "no target candidate found in the corresponding file"
