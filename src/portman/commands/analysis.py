"""Analysis/reporting commands: status, gaps, batches, report, provenance."""
from __future__ import annotations

import json
from pathlib import Path

from ._shared import _cfg, _ctx
from .. import inventory, progress, report as reportmod
from .. import provenance as prov


_REGRESSION_METRICS = ("symbol_pct", "public_api_pct", "verified_pct", "weighted_pct")


def cmd_status(args):
    cfg, db = _ctx(args)
    cov = progress.coverage(db, cfg.upstream.version, cfg)

    if args.fail_on_regression:
        prev = json.loads(Path(args.fail_on_regression).read_text())
        prev = prev.get("coverage", prev)   # accept a raw status or a coverage.json
        drops = [(k, prev.get(k, 0), cov[k]) for k in _REGRESSION_METRICS
                 if cov[k] + 0.1 < prev.get(k, 0)]
        if drops:
            for k, was, now in drops:
                print(f"REGRESSION {k}: {was}% -> {now}%")
            return 1
        print("no regression vs baseline (" + ", ".join(f"{k}={cov[k]}%" for k in _REGRESSION_METRICS) + ")")
        return 0
    if args.save:
        Path(args.save).write_text(json.dumps(cov, indent=2, sort_keys=True))
        print(f"saved status -> {args.save}")
    if args.json:
        print(json.dumps(cov, indent=2)); return
    print(f"upstream {cov['upstream_version'] or 'working'}: {cov['total_symbols']} symbols")
    print(f"  symbol coverage : {cov['symbol_pct']}%")
    print(f"  public API      : {cov['public_api_pct']}%  ({cov['public_total']} API symbols)")
    print(f"  file coverage   : {cov['file_pct']}%")
    print(f"  verified        : {cov['verified_pct']}%")
    print(f"  weighted (plan) : {cov['weighted_pct']}%")
    if cov["parse_errors"]:
        print(f"  parse errors    : {cov['parse_errors']} (excluded)")
    if cov.get("copied_total"):
        print(f"  copied/generated: {cov['copied_pct']}% of {cov['copied_total']} (separate)")
    if cov.get("ignored"):
        print(f"  ignored         : {cov['ignored']} (out of scope)")
    areas = {a: d for a, d in cov.get("by_area", {}).items() if a != "other"}
    if areas:
        print("  by area:")
        for a, d in sorted(areas.items(), key=lambda x: x[1]["pct"]):
            print(f"    {a:18} {d['pct']:5}%  ({d['done']}/{d['total']})")
    print("  by status:")
    for k, v in sorted(cov["by_status"].items(), key=lambda x: -x[1]):
        print(f"    {k:14} {v}")


def cmd_gaps(args):
    cfg, db = _ctx(args)
    # match-dependent reasons (kind_mismatch/already_mapped/link_candidate) need the
    # candidate lookup, so a --reason filter implies --explain.
    explain = args.explain or bool(args.reason)
    # filter BEFORE limiting, else --limit truncates the global list and the
    # --public/--reason filters then return fewer (or miss) matching gaps.
    gp = progress.gaps(db, cfg.upstream.version, cfg=cfg, explain=explain)
    if args.public:
        gp = [g for g in gp if g["public"]]
    if args.reason:
        gp = [g for g in gp if g.get("reason") == args.reason]
    gp = gp[:args.limit]
    if args.json:
        print(json.dumps(gp, indent=2)); return
    for g in gp:
        line = f"[{g['risk']}] {g['path']}::{g['qualname']} ({g['kind']}) {g['status']}"
        if g.get("reason"):
            line += f"  — {g['reason']}"
        print(line)
        if explain and g.get("detail"):
            print(f"        {g['detail']}")
    # reason histogram so you can see the shape of the backlog
    if cfg is not None and not args.json:
        hist = {}
        for g in gp:
            hist[g.get("reason", "?")] = hist.get(g.get("reason", "?"), 0) + 1
        print(f"-- {len(gp)} gaps  " + " ".join(f"{r}={n}" for r, n in sorted(hist.items(), key=lambda x: -x[1])))
    else:
        print(f"-- {len(gp)} gaps")


def cmd_batches(args):
    cfg, db = _ctx(args)
    bs = progress.batches(db, cfg.upstream.version, cfg, limit=args.limit,
                          public_only=args.public)
    if args.out or args.json:
        manifest = {"upstream_version": cfg.upstream.version, "batches": bs}
        text = json.dumps(manifest, indent=2, sort_keys=True)
        if args.out:
            Path(args.out).write_text(text); print(f"wrote manifest -> {args.out} ({len(bs)} batches)")
        else:
            print(text)
        return
    for b in bs:
        print(f"### {b['batch']}  [risk {b['risk']}, +{b['coverage_impact_pts']}pts, "
              f"{b['count']} symbols / {b['public_count']} public]")
        tf = b.get("target_files") or ([b["target_file"]] if b.get("target_file") else [])
        if tf:
            label = "target file" if len(tf) == 1 else f"target files ({len(tf)})"
            print(f"    {label}: {', '.join(tf[:4])}" + (" …" if len(tf) > 4 else ""))
        print(f"    reasons: " + ", ".join(f"{r}={n}" for r, n in sorted(b['reasons'].items(), key=lambda x: -x[1])))
        if b["blockers"]:
            print(f"    blockers: " + "; ".join(b["blockers"]))
        print(f"    e.g. {', '.join(s.split('::')[-1] for s in b['symbols'][:8])}"
              + (f" … +{b['count']-8}" if b["count"] > 8 else ""))
    print(f"-- {len(bs)} batches")


def cmd_report(args):
    cfg, db = _ctx(args)
    cov = reportmod.write_all(db, cfg.upstream.version, cfg.reports_dir, cfg)
    print(f"wrote {cfg.reports_dir}/dashboard.md  "
          f"(symbol {cov['symbol_pct']}%, public-API {cov['public_api_pct']}%)")


def cmd_provenance(args):
    cfg = _cfg(args)
    from ..adapters import get_adapter
    ad = get_adapter(cfg.target.adapter, cfg.generic_adapters.get(cfg.target.adapter))
    exts = inventory._upstream_exts(cfg)
    miss, legacy, canon = [], [], 0
    for f in ad.discover(cfg.target.root):
        p = prov.parse_file(f, cfg.target.root, exts)
        if p.format == "none":
            miss.append(p.target_path)
        elif p.format == "legacy":
            legacy.append(p.target_path)
        else:
            canon += 1
    print(f"canonical headers: {canon}")
    print(f"legacy headers ({len(legacy)}) — upgrade to '// @port' form:")
    for p in legacy[:args.limit]:
        print(f"  legacy  {p}")
    print(f"missing headers ({len(miss)}):")
    for p in miss[:args.limit]:
        print(f"  MISSING {p}")
