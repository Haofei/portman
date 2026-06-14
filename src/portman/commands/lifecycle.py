"""Lifecycle commands: inventory, map, snapshot, diff."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from ._shared import _ctx
from .. import inventory, diff as diffmod, report as reportmod
from ..model import Side


def cmd_inventory(args):
    cfg, db = _ctx(args)
    r = inventory.build_inventory(cfg, db, allow_parse_errors=not args.strict)
    print(f"upstream: {r['upstream_symbols']} symbols @ {cfg.upstream.version or 'working'}")
    print(f"target:   {r['target_symbols']} symbols")
    if r["parse_errors"]:
        print(f"⚠️  parse errors: {r['parse_errors']} (excluded from coverage; "
              f"see `portman doctor`). Use --strict to fail on these.")


def cmd_map(args):
    cfg, db = _ctx(args)
    r = inventory.auto_map(cfg, db)
    print(f"file pairs: {r['file_pairs']}  (header-confirmed: {r['header_confirmed']})")
    print(f"auto-linked symbols: {r['linked']}  "
          f"(ambiguous/unlinked name-collisions: {r['ambiguous']})")
    if r.get("forced_links"):
        print(f"forced symbol links (config): {r['forced_links']}")
    for miss in r.get("forced_missing", []):
        print(f"  ⚠️ symbol_link target not found: {miss}")


def cmd_snapshot(args):
    """Extract upstream at a specific git ref into the DB under that version key,
    without disturbing the working checkout (uses `git worktree`)."""
    cfg, db = _ctx(args)
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
            from ..adapters import get_adapter
            ad = get_adapter(cfg.upstream.adapter, cfg.generic_adapters.get(cfg.upstream.adapter))
            syms = ad.extract_tree(Path(tmp) / rel, Side.UPSTREAM.value, cfg.upstream.repo,
                                   sha, cfg.upstream.exclude)
            db.replace_symbols(Side.UPSTREAM.value, sha, syms)
            db.set_version_alias(ref, sha)   # so `diff <ref> ...` resolves
            print(f"snapshot {ref} ({sha[:10]}): {len(syms)} upstream symbols stored")
            print(f"  use either the ref '{ref}' or sha '{sha[:10]}' with `portman diff`")
        finally:
            subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", tmp],
                           capture_output=True)


def _resolve_diff_version(cfg, db, v: str) -> str:
    """Resolve a user-supplied version (tag/branch/sha) to the key its symbols are
    stored under: try the alias table, then `git rev-parse` in the upstream repo."""
    resolved = db.resolve_version(v)
    if db.has_version(Side.UPSTREAM.value, resolved):
        return resolved
    sha = subprocess.run(["git", "-C", str(cfg.upstream.root), "rev-parse", v],
                         capture_output=True, text=True).stdout.strip()
    if sha and db.has_version(Side.UPSTREAM.value, sha):
        return sha
    return resolved   # caller reports the missing snapshot


def cmd_diff(args):
    cfg, db = _ctx(args)
    old = _resolve_diff_version(cfg, db, args.old)
    new = _resolve_diff_version(cfg, db, args.new)
    for label, raw, res in (("old", args.old, old), ("new", args.new, new)):
        if not db.has_version(Side.UPSTREAM.value, res):
            print(f"error: no snapshot for {label} version '{raw}'. "
                  f"Run: portman snapshot --version {raw}")
            return 1
    rep = diffmod.upgrade_report(db, old, new)
    if args.json:
        print(json.dumps(rep, indent=2)); return
    out = cfg.reports_dir; out.mkdir(parents=True, exist_ok=True)
    md = reportmod.upgrade_md(rep)
    (out / f"upgrade_{old[:8]}_{new[:8]}.md").write_text(md)
    print(md)
