"""Progress, coverage, and gap analysis — the read model behind every report.

Answers the headline questions:
  - % of upstream ported (weighted + count)
  - which APIs are missing / implemented-but-unverified / diverged
  - gap ranking by dependency order + public-API importance + risk
"""
from __future__ import annotations

from collections import defaultdict

from .db import DB
from .model import Status, WEIGHT

# Kinds that constitute a library's actual API surface — what "public API
# coverage" should measure. Files/modules/tests/parse_errors are tracked but are
# NOT part of the public-API denominator.
API_KINDS = ("class", "function", "method", "constant", "type")
DONE = (Status.IMPLEMENTED, Status.VERIFIED, Status.DIVERGED, Status.DEPRECATED, Status.ALIASED)


def _status_of(db: DB, sid: str) -> str:
    m = db.mapping(sid)
    return m["status"] if m else Status.NOT_STARTED.value


def _is_test(s) -> bool:
    return s["kind"] == "test" or s["path"].startswith("test") or "/test" in s["path"]


def coverage(db: DB, up_version: str) -> dict:
    """Report SEPARATE coverage dimensions rather than one blended number, so
    'implemented' never masquerades as 'API-complete' or 'verified'."""
    syms = db.symbols("upstream", up_version)
    by_status: dict[str, int] = defaultdict(int)
    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    weighted = 0.0
    # dimension counters: (done, total)
    dim = {k: [0, 0] for k in ("file", "symbol", "public_api", "test", "verified")}
    parse_errors = 0
    scored = 0
    for s in syms:
        if s["kind"] == "parse_error":
            parse_errors += 1
            continue                       # not healthy inventory; excluded from %
        st = _status_of(db, s["sid"])
        done = Status(st) in DONE
        verified = Status(st) in (Status.VERIFIED, Status.DIVERGED, Status.DEPRECATED)
        by_status[st] += 1
        by_kind[s["kind"]][st] += 1
        weighted += WEIGHT[Status(st)]
        scored += 1
        # every real symbol counts toward "symbol" coverage
        dim["symbol"][1] += 1; dim["symbol"][0] += done
        dim["verified"][1] += 1; dim["verified"][0] += verified
        if s["kind"] in ("file", "module"):
            dim["file"][1] += 1; dim["file"][0] += done
        if _is_test(s):
            dim["test"][1] += 1; dim["test"][0] += done
        elif s["kind"] in API_KINDS and s["is_public"]:
            dim["public_api"][1] += 1; dim["public_api"][0] += done

    def pct(d, t):
        return round(100 * d / t, 1) if t else 0.0

    return {
        "upstream_version": up_version,
        "total_symbols": scored,
        "parse_errors": parse_errors,
        "weighted_pct": round(100 * weighted / scored, 1) if scored else 0.0,
        "by_status": dict(by_status),
        "by_kind": {k: dict(v) for k, v in by_kind.items()},
        # explicit, non-collapsed dimensions
        "file_pct": pct(*dim["file"]),
        "symbol_pct": pct(*dim["symbol"]),
        "public_api_pct": pct(*dim["public_api"]),
        "public_total": dim["public_api"][1],
        "test_pct": pct(*dim["test"]),
        "test_total": dim["test"][1],
        "verified_pct": pct(*dim["verified"]),
    }


def _risk(s, high: tuple[str, ...] = (), medium: tuple[str, ...] = ()) -> int:
    """Higher = port sooner. Foundational-path bonuses come from config, not
    hard-coded module names, so the framework stays library-agnostic."""
    score = 0
    if s["is_public"]:
        score += 3
    if any(c in s["path"] for c in high):
        score += 3
    elif any(c in s["path"] for c in medium):
        score += 1
    if s["kind"] in ("class", "type"):
        score += 1
    return score


def gaps(db: DB, up_version: str, limit: int | None = None,
         risk_high: tuple[str, ...] = (), risk_medium: tuple[str, ...] = ()) -> list[dict]:
    """Unported/partial upstream symbols, ranked by risk then path."""
    out = []
    for s in db.symbols("upstream", up_version):
        if s["kind"] == "parse_error":
            continue
        st = _status_of(db, s["sid"])
        if Status(st) in (Status.VERIFIED, Status.DIVERGED, Status.DEPRECATED, Status.ALIASED):
            continue
        if WEIGHT[Status(st)] >= 0.85:
            continue  # implemented but unverified is a *verification* gap, not a port gap
        out.append({"path": s["path"], "qualname": s["qualname"] or "<file>",
                    "kind": s["kind"], "status": st, "public": bool(s["is_public"]),
                    "risk": _risk(s, risk_high, risk_medium)})
    out.sort(key=lambda g: (-g["risk"], g["path"], g["qualname"]))
    return out[:limit] if limit else out


def unverified(db: DB, up_version: str) -> list[dict]:
    """Implemented but not yet behaviorally verified — the verification backlog."""
    out = []
    for s in db.symbols("upstream", up_version):
        m = db.mapping(s["sid"])
        if m and m["status"] == Status.IMPLEMENTED.value:
            out.append({"path": s["path"], "qualname": s["qualname"] or "<file>",
                        "verification": m["verification"], "owner": m["owner"]})
    return out


def diverged(db: DB, up_version: str) -> list[dict]:
    out = []
    for s in db.symbols("upstream", up_version):
        m = db.mapping(s["sid"])
        if m and m["status"] == Status.DIVERGED.value:
            out.append({"path": s["path"], "qualname": s["qualname"] or "<file>",
                        "deviation_id": m["deviation_id"], "note": m["note"]})
    return out


def aliases(db: DB, up_version: str) -> list[dict]:
    """Upstream symbols intentionally covered by another symbol's target
    (alias / private forwarder / public wrapper)."""
    out = []
    sym_by_sid = {s["sid"]: s for s in db.symbols("upstream", up_version)}
    for s in db.symbols("upstream", up_version):
        m = db.mapping(s["sid"])
        if m and m["status"] == Status.ALIASED.value:
            primary = sym_by_sid.get(m["covers"])
            out.append({"path": s["path"], "qualname": s["qualname"] or "<file>",
                        "covers": (f"{primary['path']}::{primary['qualname']}"
                                   if primary else m["covers"]),
                        "note": m["note"]})
    return out


def ambiguous(db: DB, up_version: str) -> list[dict]:
    """Upstream symbols whose only target match was a name collision (e.g. several
    classes' `init_hw` onto one `fn init_hw`). Deliberately NOT counted as ported;
    a human must disambiguate or record a deviation."""
    out = []
    for s in db.symbols("upstream", up_version):
        m = db.mapping(s["sid"])
        if m and m["confidence"] == "ambiguous":
            out.append({"path": s["path"], "qualname": s["qualname"] or "<file>",
                        "note": m["note"]})
    return out
