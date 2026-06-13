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


def _status_of(db: DB, sid: str) -> str:
    m = db.mapping(sid)
    return m["status"] if m else Status.NOT_STARTED.value


def coverage(db: DB, up_version: str) -> dict:
    syms = db.symbols("upstream", up_version)
    by_status: dict[str, int] = defaultdict(int)
    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    weighted = 0.0
    public_total = public_done = 0
    for s in syms:
        st = _status_of(db, s["sid"])
        by_status[st] += 1
        by_kind[s["kind"]][st] += 1
        weighted += WEIGHT[Status(st)]
        if s["is_public"]:
            public_total += 1
            if WEIGHT[Status(st)] >= 0.85:
                public_done += 1
    n = len(syms)
    return {
        "upstream_version": up_version,
        "total_symbols": n,
        "weighted_pct": round(100 * weighted / n, 1) if n else 0.0,
        "by_status": dict(by_status),
        "by_kind": {k: dict(v) for k, v in by_kind.items()},
        "public_api_pct": round(100 * public_done / public_total, 1) if public_total else 0.0,
        "public_total": public_total,
        "verified_pct": round(100 * by_status.get("verified", 0) / n, 1) if n else 0.0,
    }


def _risk(s) -> int:
    """Higher = port sooner. Public API and foundational files rank up."""
    score = 0
    if s["is_public"]:
        score += 3
    # foundational modules tend to be shallow paths / core names
    core = ("dtype", "uop/ops", "tensor", "device", "helpers")
    if any(c in s["path"] for c in core):
        score += 3
    if s["kind"] in ("class", "type"):
        score += 1
    return score


def gaps(db: DB, up_version: str, limit: int | None = None) -> list[dict]:
    """Unported/partial upstream symbols, ranked by risk then path."""
    out = []
    for s in db.symbols("upstream", up_version):
        st = _status_of(db, s["sid"])
        if Status(st) in (Status.VERIFIED, Status.DIVERGED, Status.DEPRECATED):
            continue
        if WEIGHT[Status(st)] >= 0.85:
            continue  # implemented but unverified is a *verification* gap, not a port gap
        out.append({"path": s["path"], "qualname": s["qualname"] or "<file>",
                    "kind": s["kind"], "status": st, "public": bool(s["is_public"]),
                    "risk": _risk(s)})
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
