"""Progress, coverage, and gap analysis — the read model behind every report.

Answers the headline questions:
  - % of upstream ported (weighted + count)
  - which APIs are missing / implemented-but-unverified / diverged
  - gap ranking by dependency order + public-API importance + risk
"""
from __future__ import annotations

from collections import defaultdict, Counter

from .db import DB
from .model import Status, WEIGHT
from . import inventory, matching
from . import classify

_BLOCKER = {
    "alias_needed": "disambiguate name collisions (alias/link)",
    "kind_mismatch": "kind mismatches — add forced symbol links",
    "link_candidate": "add forced symbol links (close target names exist)",
    "type_only": "type/constant — may need links or a type port",
    "already_mapped": "target taken by another upstream — review uniqueness",
    "missing": "no target yet — port from scratch",
}

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


def coverage(db: DB, up_version: str, cfg=None) -> dict:
    """Report SEPARATE coverage dimensions rather than one blended number, so
    'implemented' never masquerades as 'API-complete' or 'verified'. When `cfg`
    is given, ignored/copied symbols are segmented out of the denominators and a
    per-source-area breakdown is added."""
    areas = cfg.areas if cfg else {}
    copied_roots = cfg.copied_roots if cfg else ()
    ignore = cfg.ignore if cfg else {}
    syms = db.symbols("upstream", up_version)
    m_by_sid = db.mapping_index()
    by_status: dict[str, int] = defaultdict(int)
    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_area: dict[str, list] = defaultdict(lambda: [0, 0])   # area -> [done, total]
    weighted = 0.0
    dim = {k: [0, 0] for k in ("file", "symbol", "public_api", "test", "verified")}
    parse_errors = ignored = 0
    copied = [0, 0]
    scored = 0
    for s in syms:
        if s["kind"] == "parse_error":
            parse_errors += 1
            continue                       # not healthy inventory; excluded from %
        path, qual = s["path"], s["qualname"] or ""
        if classify.ignore_reason(path, qual, ignore) is not None:
            ignored += 1                   # out of scope; excluded from every %
            continue
        m = m_by_sid.get(s["sid"])
        st = m["status"] if m else Status.NOT_STARTED.value
        done = Status(st) in DONE
        if classify.is_copied(path, copied_roots):
            copied[1] += 1; copied[0] += done   # tracked separately, not in main %
            continue
        verified = Status(st) in (Status.VERIFIED, Status.DIVERGED, Status.DEPRECATED)
        by_status[st] += 1
        by_kind[s["kind"]][st] += 1
        weighted += WEIGHT[Status(st)]
        scored += 1
        dim["symbol"][1] += 1; dim["symbol"][0] += done
        dim["verified"][1] += 1; dim["verified"][0] += verified
        if s["kind"] in ("file", "module"):
            dim["file"][1] += 1; dim["file"][0] += done
        if _is_test(s):
            dim["test"][1] += 1; dim["test"][0] += done
        elif s["kind"] in API_KINDS and s["is_public"]:
            dim["public_api"][1] += 1; dim["public_api"][0] += done
        a = classify.area_of(path, areas)
        by_area[a][1] += 1; by_area[a][0] += done

    def pct(d, t):
        return round(100 * d / t, 1) if t else 0.0

    return {
        "upstream_version": up_version,
        "total_symbols": scored,
        "parse_errors": parse_errors,
        "ignored": ignored,
        "copied_done": copied[0], "copied_total": copied[1],
        "copied_pct": pct(*copied),
        "weighted_pct": round(100 * weighted / scored, 1) if scored else 0.0,
        "by_status": dict(by_status),
        "by_kind": {k: dict(v) for k, v in by_kind.items()},
        "by_area": {a: {"done": d, "total": t, "pct": pct(d, t)}
                    for a, (d, t) in sorted(by_area.items())},
        # explicit, non-collapsed dimensions
        "file_pct": pct(*dim["file"]),
        "symbol_pct": pct(*dim["symbol"]),
        "public_api_pct": pct(*dim["public_api"]),
        "public_total": dim["public_api"][1],
        "test_pct": pct(*dim["test"]),
        "test_total": dim["test"][1],
        "verified_pct": pct(*dim["verified"]),
    }


def _risk(s, high: tuple[str, ...] = (), medium: tuple[str, ...] = (),
          dep_boost: tuple[str, ...] = ()) -> int:
    """Higher = port sooner. Foundational-path bonuses + manual dependency hints
    come from config, not hard-coded module names."""
    score = 0
    if s["is_public"]:
        score += 3
    if any(c in s["path"] for c in high):
        score += 3
    elif any(c in s["path"] for c in medium):
        score += 1
    if s["kind"] in ("class", "type"):
        score += 1
    if any(classify._spec_matches(s["path"], s["qualname"] or "", spec) for spec in dep_boost):
        score += 5                          # unlocks downstream work (#8)
    return score


# gap_reasons that are intentional/out-of-scope, not real port work
_SEGMENTED_REASONS = ("ignored", "copied_generated")


def gaps(db: DB, up_version: str, limit: int | None = None, cfg=None,
         risk_high: tuple[str, ...] = (), risk_medium: tuple[str, ...] = (),
         explain: bool = False) -> list[dict]:
    """Unported upstream symbols, ranked by risk. With `cfg`, each gap is tagged
    with a `reason` (see classify.gap_reason); ignored/copied are dropped. With
    `explain`, the closest in-file target candidate is computed and attached."""
    high = cfg.risk_high if cfg else risk_high
    medium = cfg.risk_medium if cfg else risk_medium
    dep_boost = cfg.dep_boost if cfg else ()
    m_by_sid = db.mapping_index()

    tgt_by_uppath, rules, target_owner = {}, None, {}
    if explain and cfg:
        fc = inventory.file_correspondence(cfg, db)
        tgt_by_uppath = fc["tgt_by_uppath"]
        rules = inventory.build_rules(cfg)
        up_by_sid = {s["sid"]: s for s in fc["up_syms"]}
        for m in m_by_sid.values():
            if m["target_sid"]:
                us = up_by_sid.get(m["upstream_sid"])
                target_owner[m["target_sid"]] = (f"{us['path']}::{us['qualname']}"
                                                 if us else m["upstream_sid"])

    out = []
    for s in db.symbols("upstream", up_version):
        if s["kind"] == "parse_error":
            continue
        st = m_by_sid[s["sid"]]["status"] if s["sid"] in m_by_sid else Status.NOT_STARTED.value
        if Status(st) in (Status.VERIFIED, Status.DIVERGED, Status.DEPRECATED, Status.ALIASED):
            continue
        if WEIGHT[Status(st)] >= 0.85:
            continue  # implemented but unverified is a *verification* gap, not a port gap

        g = {"path": s["path"], "qualname": s["qualname"] or "<file>",
             "kind": s["kind"], "status": st, "public": bool(s["is_public"]),
             "risk": _risk(s, high, medium, dep_boost)}
        if cfg is not None:
            tm = (matching.best_target_candidate(s, tgt_by_uppath, rules, target_owner)
                  if (explain and rules is not None) else None)
            reason, detail = classify.gap_reason(s, st, m_by_sid.get(s["sid"]), tm, cfg)
            if reason in _SEGMENTED_REASONS:
                continue                    # not a real port gap; segmented in coverage
            g["reason"] = reason
            if explain:
                g["detail"] = detail
                g["target_candidate"] = tm["qualname"] if tm else None
                g["target_candidate_path"] = tm["path"] if tm else None
        out.append(g)
    out.sort(key=lambda g: (-g["risk"], g["path"], g["qualname"]))
    return out[:limit] if limit else out


def batches(db: DB, up_version: str, cfg, limit: int | None = None,
            public_only: bool = False) -> list[dict]:
    """Group related gaps into coherent port batches (#3). Symbols are grouped by
    (upstream file, owner class) so you get 'UOp methods', 'UPat methods', etc.
    Each batch carries its suggested target file, blockers, coverage impact, and a
    verification command — i.e. it doubles as the machine-readable manifest (#9)."""
    fc = inventory.file_correspondence(cfg, db)
    uppath_to_tgtpaths = fc["uppath_to_tgtpaths"]   # split ports: all target files
    cov = coverage(db, up_version, cfg)
    sym_total = max(cov["total_symbols"], 1)

    gp = gaps(db, up_version, cfg=cfg, explain=True)
    groups: dict[tuple, list] = defaultdict(list)
    for g in gp:
        if public_only and not g["public"]:
            continue
        qual = g["qualname"]
        owner = qual.rsplit(".", 1)[0] if "." in qual else "<module>"
        groups[(g["path"], owner)].append(g)

    out = []
    for (path, owner), members in groups.items():
        reasons = Counter(m.get("reason", "missing") for m in members)
        blockers = sorted({_BLOCKER[r] for r in reasons if r in _BLOCKER})
        # Suggest where this batch actually lands (a LIST — split ports span files).
        # MERGE the files the batch's candidates live in (concrete, by frequency)
        # with the upstream file's mirror file(s): a batch often mixes symbols with
        # a candidate and symbols with none (e.g. UOp.alu/const are missing), and
        # the no-candidate ones still belong in the mirror files.
        cand_paths = Counter(m["target_candidate_path"] for m in members
                             if m.get("target_candidate_path"))
        target_files = [p for p, _ in cand_paths.most_common()]
        for p in uppath_to_tgtpaths.get(path, []):
            if p not in target_files:
                target_files.append(p)
        out.append({
            "batch": f"{owner} in {path}",
            "upstream_path": path,
            "owner": owner,
            "target_files": target_files,
            "target_file": target_files[0] if target_files else "",
            "symbols": [f"{m['path']}::{m['qualname']}" for m in members],
            "count": len(members),
            "public_count": sum(1 for m in members if m["public"]),
            "reasons": dict(reasons),
            "blockers": blockers,
            "risk": sum(m["risk"] for m in members),
            "coverage_impact_pts": round(100 * len(members) / sym_total, 2),
            "verify": cfg.verify_command or "<configure [verify].command>",
        })
    out.sort(key=lambda b: (-b["risk"], -b["count"]))
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
