"""Curation commands: set, alias, link, trace, export, import."""
from __future__ import annotations

from ._shared import _cfg, _ctx
from ..db import DB
from ..model import Mapping, symbol_id, Status, Confidence, Side


def cmd_set(args):
    if args.status == Status.ALIASED.value:   # guard programmatic callers; argparse blocks the CLI
        print("error: use `portman alias A --of B` to create an aliased mapping "
              "(it needs a `covers` target that `set` cannot supply).")
        return 1
    cfg, db = _ctx(args)
    path, _, qual = args.upstream.partition("::")
    sid = symbol_id(cfg.upstream.repo, path, qual, args.kind)
    m = db.mapping(sid)
    mm = Mapping(upstream_sid=sid, status=args.status,
                 target_sid=m["target_sid"] if m else None,
                 verification=args.verification or (m["verification"] if m else "none"),
                 owner=args.owner or (m["owner"] if m else ""),
                 deviation_id=args.deviation or (m["deviation_id"] if m else None),
                 note=args.note or (m["note"] if m else ""),
                 confidence=Confidence.MANUAL.value)
    db.upsert_mapping(mm)
    db.export_curated(cfg.root / "mappings" / "curated.jsonl")
    print(f"set {args.upstream} -> {args.status} (curated.jsonl updated)")


def _resolve_upstream_sid(db, cfg, spec: str, kind: str):
    """Resolve a symbol spec to a sid. Accepts an exact 'path::Qualname' or a bare
    'Qualname' that is searched within the upstream inventory. Returns
    (sid, error_message)."""
    if "::" in spec:
        path, _, qual = spec.partition("::")
        return symbol_id(cfg.upstream.repo, path, qual, kind), None
    matches = [s for s in db.symbols(Side.UPSTREAM.value, cfg.upstream.version)
               if s["qualname"] == spec and (not kind or s["kind"] == kind)]
    if len(matches) == 1:
        return matches[0]["sid"], None
    if not matches:
        return None, f"'{spec}' not found in upstream (kind={kind})"
    opts = ", ".join(f"{m['path']}::{m['qualname']}" for m in matches[:6])
    return None, f"'{spec}' is ambiguous ({len(matches)} matches) — qualify as path::Qualname: {opts}"


def cmd_alias(args):
    """Mark an upstream symbol as covered by another symbol's target implementation
    (an alias / private forwarder / public wrapper), without violating target
    uniqueness. Accepts bare qualnames or path::Qualname.
    Example: portman alias 'Tensor._data' --of 'Tensor.data'."""
    cfg, db = _ctx(args)
    alias_sid, err = _resolve_upstream_sid(db, cfg, args.alias, args.kind)
    if err:
        print(f"error: {err}"); return 1
    primary_sid, err = _resolve_upstream_sid(db, cfg, args.of, args.of_kind or args.kind)
    if err:
        print(f"error: {err}"); return 1
    pm = db.mapping(primary_sid)
    if not pm or not pm["target_sid"]:
        print(f"error: primary '{args.of}' has no target mapping yet — map/verify it first.")
        return 1
    db.upsert_mapping(Mapping(
        upstream_sid=alias_sid, target_sid=pm["target_sid"],
        status=Status.ALIASED.value, covers=primary_sid, confidence=Confidence.MANUAL.value,
        note=args.note or f"alias of {args.of}"))
    db.export_curated(cfg.root / "mappings" / "curated.jsonl")
    print(f"aliased {args.alias} -> covered by {args.of} (target preserved; "
          f"curated.jsonl updated)")


def cmd_link(args):
    """Force a link from an upstream symbol to a specific target symbol, for names
    the matcher can't bridge. This writes a durable manual link to curated.jsonl
    (for one-offs); for bulk conventions use [mapping.symbol_links] in config.
    Example: portman link 'helpers.py::count' 'helpers.rss::helpers_count'."""
    cfg, db = _ctx(args)
    up_p, _, up_q = args.upstream.partition("::")
    tg_p, _, tg_q = args.target.partition("::")
    u = next((s for s in db.symbols(Side.UPSTREAM.value, cfg.upstream.version)
              if s["path"] == up_p and s["qualname"] == up_q), None)
    t = next((s for s in db.symbols(Side.TARGET.value, cfg.target.version)
              if s["path"] == tg_p and s["qualname"] == tg_q), None)
    if not u:
        print(f"error: upstream '{args.upstream}' not found"); return 1
    if not t:
        print(f"error: target '{args.target}' not found"); return 1
    db.upsert_mapping(Mapping(upstream_sid=u["sid"], target_sid=t["sid"],
                              status=Status.IMPLEMENTED.value, confidence=Confidence.MANUAL.value,
                              note=args.note or f"forced link to {args.target}"))
    db.export_curated(cfg.root / "mappings" / "curated.jsonl")
    print(f"linked {args.upstream} -> {args.target} (curated.jsonl updated)")


def cmd_trace(args):
    cfg, db = _ctx(args)
    path, _, qual = args.target.partition("::")
    found = False
    for s in db.symbols(Side.UPSTREAM.value, cfg.upstream.version):
        if s["path"] == path and (not qual or s["qualname"] == qual):
            m = db.mapping(s["sid"])
            print(f"UPSTREAM {cfg.upstream.repo}@{(cfg.upstream.version or 'working')[:10]}")
            print(f"  {s['path']}::{s['qualname'] or '<file>'} ({s['kind']})  L{s['lineno']}")
            print(f"  signature: {s['signature']}")
            if m:
                print(f"  status={m['status']} verification={m['verification']} "
                      f"confidence={m['confidence']} owner={m['owner'] or '-'}")
                if m["target_sid"]:
                    t = db.c.execute(
                        "SELECT * FROM symbols WHERE sid=? AND side='target' AND version=?",
                        (m["target_sid"], cfg.target.version)).fetchone()
                    if t:
                        print(f"  -> TARGET {t['path']}::{t['qualname']} L{t['lineno']}")
                if m["covers"]:
                    p = db.c.execute(
                        "SELECT path,qualname FROM symbols WHERE sid=? AND side='upstream' AND version=?",
                        (m["covers"], cfg.upstream.version)).fetchone()
                    primary = f"{p['path']}::{p['qualname']}" if p else m["covers"]
                    print(f"  covered-by (alias of): {primary}")
                if m["deviation_id"]:
                    print(f"  deviation: {m['deviation_id']} — {m['note']}")
            else:
                print("  (no mapping)")
            found = True
    if not found:
        print("no upstream symbol matched")


def cmd_export(args):
    cfg, db = _ctx(args)
    db.export_curated(cfg.root / "mappings" / "curated.jsonl")
    print("exported curated facts -> mappings/curated.jsonl")


def cmd_import(args):
    """Re-load curated facts from mappings/curated.jsonl into the DB. (Also done
    automatically on every DB open; this is the explicit form.)"""
    cfg = _cfg(args); db = DB(cfg.db_path)
    path = cfg.root / "mappings" / "curated.jsonl"
    db.import_curated(path)
    print(f"imported curated facts <- {path}")
