"""Markdown + JSON report generation. Pure functions of the read model so the
same data drives CI summaries, the committed dashboard, and machine consumers."""
from __future__ import annotations

import json
from pathlib import Path

from .db import DB
from . import progress, diff as diffmod


def _bar(pct: float, width: int = 24) -> str:
    fill = int(round(pct / 100 * width))
    return "█" * fill + "░" * (width - fill)


def dashboard_md(db: DB, up_version: str) -> str:
    cov = progress.coverage(db, up_version)
    gp = progress.gaps(db, up_version, limit=25)
    unv = progress.unverified(db, up_version)
    dv = progress.diverged(db, up_version)
    L = []
    L.append(f"# Port Dashboard — upstream `{up_version}`\n")
    L.append(f"**Overall (weighted):** {cov['weighted_pct']}%  `{_bar(cov['weighted_pct'])}`")
    L.append(f"**Public API ported:** {cov['public_api_pct']}% of {cov['public_total']}  "
             f"`{_bar(cov['public_api_pct'])}`")
    L.append(f"**Verified:** {cov['verified_pct']}%  `{_bar(cov['verified_pct'])}`\n")

    L.append("## Status breakdown\n")
    L.append("| Status | Count |\n|---|---:|")
    for k, v in sorted(cov["by_status"].items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} |")
    L.append(f"| **total** | **{cov['total_symbols']}** |\n")

    L.append("## Coverage by kind\n")
    L.append("| Kind | implemented+ | total |\n|---|---:|---:|")
    for kind, sts in sorted(cov["by_kind"].items()):
        tot = sum(sts.values())
        done = sum(c for s, c in sts.items() if s in ("implemented", "verified", "diverged", "deprecated"))
        L.append(f"| {kind} | {done} | {tot} |")
    L.append("")

    L.append(f"## Top port gaps (ranked by risk) — {len(gp)} shown of unported\n")
    L.append("| Risk | Path | Symbol | Kind | Status | Public |\n|---:|---|---|---|---|:--:|")
    for g in gp:
        L.append(f"| {g['risk']} | `{g['path']}` | `{g['qualname']}` | {g['kind']} "
                 f"| {g['status']} | {'✓' if g['public'] else ''} |")
    L.append("")

    L.append(f"## Verification backlog (implemented, not verified) — {len(unv)}\n")
    for u in unv[:25]:
        L.append(f"- `{u['path']}` `{u['qualname']}` — verification: {u['verification'] or 'none'}"
                 + (f" — owner {u['owner']}" if u['owner'] else ""))
    if len(unv) > 25:
        L.append(f"- … +{len(unv) - 25} more")
    L.append("")

    L.append(f"## Documented deviations — {len(dv)}\n")
    for d in dv:
        L.append(f"- `{d['path']}` `{d['qualname']}` — {d['deviation_id'] or '(no id)'}: {d['note']}")
    L.append("")
    return "\n".join(L)


def upgrade_md(report: dict) -> str:
    s = report["summary"]
    L = [f"# Upstream Upgrade Report — `{report['old_version']}` → `{report['new_version']}`\n",
         f"- added: **{s['added']}**  removed: **{s['removed']}**  moved: **{s['moved']}**  "
         f"signature-changed: **{s['signature_changed']}**  body-changed: **{s['body_changed']}**\n"]
    def tbl(title, items, cols):
        L.append(f"## {title} — {len(items)}\n")
        if not items:
            L.append("_none_\n"); return
        L.append("| " + " | ".join(cols) + " |")
        L.append("|" + "|".join("---" for _ in cols) + "|")
        for it in items[:60]:
            L.append("| " + " | ".join(f"`{it.get(c,'')}`" for c in cols) + " |")
        if len(items) > 60:
            L.append(f"\n_… +{len(items)-60} more_")
        L.append("")
    tbl("New upstream surface to port", report["new_work"], ["path", "qualname", "kind"])
    tbl("Ported symbols needing RE-VERIFICATION (upstream changed)",
        report["needs_reverify"], ["path", "qualname", "current_status", "owner"])
    tbl("Candidate deprecations (upstream removed, we still implement)",
        report["candidate_deprecations"], ["path", "qualname", "kind"])
    tbl("Moved files/symbols", report["moved"], ["from", "to", "qualname"])
    return "\n".join(L)


def write_all(db: DB, up_version: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    cov = progress.coverage(db, up_version)
    (out / "dashboard.md").write_text(dashboard_md(db, up_version))
    (out / "coverage.json").write_text(json.dumps({
        "coverage": cov,
        "gaps": progress.gaps(db, up_version),
        "unverified": progress.unverified(db, up_version),
        "diverged": progress.diverged(db, up_version),
    }, indent=2))
    db.add_snapshot(up_version, {
        "weighted_pct": cov["weighted_pct"],
        "public_api_pct": cov["public_api_pct"],
        "verified_pct": cov["verified_pct"]})
    return cov
