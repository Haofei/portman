"""portman CLI — the single entry point for the whole lifecycle.

  portman inventory                 extract upstream + target into the DB
  portman map                       auto-link via provenance + name matching
  portman status [--json]           print the coverage summary
  portman gaps [--limit N] [--public]   ranked port gaps
  portman report                    write reports/dashboard.md + coverage.json
  portman provenance lint           list target files missing/with-legacy headers
  portman snapshot --version REF    re-extract upstream at a git ref into the DB
  portman diff OLD NEW              upstream change report between two snapshots
  portman set STATUS --upstream ... manually set a mapping's status/owner (curated)
  portman trace PATH[:QUALNAME]     show the full provenance/verification record
  portman export / import           sync curated facts to mappings/curated.jsonl
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import Config
from .db import DB
from . import inventory, progress, diff as diffmod, report as reportmod
from . import provenance as prov
from .model import Mapping, Symbol, symbol_id, Status


def _cfg(args) -> Config:
    return Config.load(Path(args.config))


def _db(cfg: Config) -> DB:
    db = DB(cfg.db_path)
    db.import_curated(cfg.root / "mappings" / "curated.jsonl")
    return db


def cmd_inventory(args):
    cfg = _cfg(args); db = _db(cfg)
    r = inventory.build_inventory(cfg, db)
    print(f"upstream: {r['upstream_symbols']} symbols @ {cfg.upstream.version or 'working'}")
    print(f"target:   {r['target_symbols']} symbols")


def cmd_map(args):
    cfg = _cfg(args); db = _db(cfg)
    r = inventory.auto_map(cfg, db)
    print(f"file pairs: {r['file_pairs']}  (header-confirmed: {r['header_confirmed']})")
    print(f"auto-linked symbols: {r['linked']}  (proposed implemented: {r['proposed']})")


def cmd_status(args):
    cfg = _cfg(args); db = _db(cfg)
    cov = progress.coverage(db, cfg.upstream.version)
    if args.json:
        print(json.dumps(cov, indent=2)); return
    print(f"upstream {cov['upstream_version'] or 'working'}: {cov['total_symbols']} symbols")
    print(f"  weighted ported : {cov['weighted_pct']}%")
    print(f"  public API      : {cov['public_api_pct']}%  ({cov['public_total']} public)")
    print(f"  verified        : {cov['verified_pct']}%")
    print("  by status:")
    for k, v in sorted(cov["by_status"].items(), key=lambda x: -x[1]):
        print(f"    {k:14} {v}")


def cmd_gaps(args):
    cfg = _cfg(args); db = _db(cfg)
    gp = progress.gaps(db, cfg.upstream.version, limit=args.limit)
    if args.public:
        gp = [g for g in gp if g["public"]]
    for g in gp:
        print(f"[{g['risk']}] {g['path']}::{g['qualname']} ({g['kind']}) {g['status']}")
    print(f"-- {len(gp)} gaps")


def cmd_report(args):
    cfg = _cfg(args); db = _db(cfg)
    cov = reportmod.write_all(db, cfg.upstream.version, cfg.reports_dir)
    print(f"wrote {cfg.reports_dir}/dashboard.md  ({cov['weighted_pct']}% weighted)")


def cmd_provenance(args):
    cfg = _cfg(args)
    from .adapters import get_adapter
    ad = get_adapter(cfg.target.adapter, cfg.generic_adapters.get(cfg.target.adapter))
    miss, legacy, canon = [], [], 0
    for f in ad.discover(cfg.target.root):
        p = prov.parse_file(f, cfg.target.root)
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


def cmd_snapshot(args):
    """Extract upstream at a specific git ref into the DB under that version key,
    without disturbing the working checkout (uses `git worktree`)."""
    cfg = _cfg(args); db = _db(cfg)
    ref = args.version
    up_root = cfg.upstream.root
    # find repo root
    repo = subprocess.run(["git", "-C", str(up_root), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True).stdout.strip()
    sha = subprocess.run(["git", "-C", repo, "rev-parse", ref],
                         capture_output=True, text=True).stdout.strip()
    rel = up_root.resolve().relative_to(Path(repo).resolve())
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "-C", repo, "worktree", "add", "--detach", tmp, sha],
                       check=True, capture_output=True)
        try:
            from .adapters import get_adapter
            ad = get_adapter(cfg.upstream.adapter, cfg.generic_adapters.get(cfg.upstream.adapter))
            syms = ad.extract_tree(Path(tmp) / rel, "upstream", cfg.upstream.repo,
                                   sha, cfg.upstream.exclude)
            db.replace_symbols("upstream", sha, syms)
            print(f"snapshot {ref} ({sha[:10]}): {len(syms)} upstream symbols stored")
        finally:
            subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", tmp],
                           capture_output=True)


def cmd_diff(args):
    cfg = _cfg(args); db = _db(cfg)
    rep = diffmod.upgrade_report(db, args.old, args.new)
    if args.json:
        print(json.dumps(rep, indent=2)); return
    out = cfg.reports_dir; out.mkdir(parents=True, exist_ok=True)
    md = reportmod.upgrade_md(rep)
    (out / f"upgrade_{args.old[:8]}_{args.new[:8]}.md").write_text(md)
    print(md)


def cmd_set(args):
    cfg = _cfg(args); db = _db(cfg)
    path, _, qual = args.upstream.partition("::")
    sid = symbol_id(cfg.upstream.repo, path, qual, args.kind)
    m = db.mapping(sid)
    mm = Mapping(upstream_sid=sid, status=args.status,
                 target_sid=m["target_sid"] if m else None,
                 verification=args.verification or (m["verification"] if m else "none"),
                 owner=args.owner or (m["owner"] if m else ""),
                 deviation_id=args.deviation or (m["deviation_id"] if m else None),
                 note=args.note or (m["note"] if m else ""),
                 confidence="manual")
    db.upsert_mapping(mm)
    db.export_curated(cfg.root / "mappings" / "curated.jsonl")
    print(f"set {args.upstream} -> {args.status} (curated.jsonl updated)")


def cmd_trace(args):
    cfg = _cfg(args); db = _db(cfg)
    path, _, qual = args.target.partition("::")
    found = False
    for s in db.symbols("upstream", cfg.upstream.version):
        if s["path"] == path and (not qual or s["qualname"] == qual):
            m = db.mapping(s["sid"])
            print(f"UPSTREAM {cfg.upstream.repo}@{(cfg.upstream.version or 'working')[:10]}")
            print(f"  {s['path']}::{s['qualname'] or '<file>'} ({s['kind']})  L{s['lineno']}")
            print(f"  signature: {s['signature']}")
            if m:
                print(f"  status={m['status']} verification={m['verification']} "
                      f"confidence={m['confidence']} owner={m['owner'] or '-'}")
                if m["target_sid"]:
                    t = db.c.execute("SELECT * FROM symbols WHERE sid=? AND side='target'",
                                     (m["target_sid"],)).fetchone()
                    if t:
                        print(f"  -> TARGET {t['path']}::{t['qualname']} L{t['lineno']}")
                if m["deviation_id"]:
                    print(f"  deviation: {m['deviation_id']} — {m['note']}")
            else:
                print("  (no mapping)")
            found = True
    if not found:
        print("no upstream symbol matched")


def cmd_export(args):
    cfg = _cfg(args); db = _db(cfg)
    db.export_curated(cfg.root / "mappings" / "curated.jsonl")
    print("exported curated facts -> mappings/curated.jsonl")


def build_parser():
    p = argparse.ArgumentParser(prog="portman", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="portman.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("inventory").set_defaults(func=cmd_inventory)
    sub.add_parser("map").set_defaults(func=cmd_map)
    s = sub.add_parser("status"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_status)
    s = sub.add_parser("gaps"); s.add_argument("--limit", type=int, default=40)
    s.add_argument("--public", action="store_true"); s.set_defaults(func=cmd_gaps)
    sub.add_parser("report").set_defaults(func=cmd_report)
    s = sub.add_parser("provenance"); s.add_argument("action", choices=["lint"], nargs="?", default="lint")
    s.add_argument("--limit", type=int, default=30); s.set_defaults(func=cmd_provenance)
    s = sub.add_parser("snapshot"); s.add_argument("--version", required=True); s.set_defaults(func=cmd_snapshot)
    s = sub.add_parser("diff"); s.add_argument("old"); s.add_argument("new")
    s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_diff)
    s = sub.add_parser("set"); s.add_argument("status", choices=[x.value for x in Status])
    s.add_argument("--upstream", required=True); s.add_argument("--kind", default="function")
    s.add_argument("--verification", default=""); s.add_argument("--owner", default="")
    s.add_argument("--deviation", default=""); s.add_argument("--note", default=""); s.set_defaults(func=cmd_set)
    s = sub.add_parser("trace"); s.add_argument("target"); s.set_defaults(func=cmd_trace)
    sub.add_parser("export").set_defaults(func=cmd_export)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
