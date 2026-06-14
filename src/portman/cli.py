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
  portman alias A --of B            mark upstream A as covered by B's target (alias)
  portman trace PATH[::QUALNAME]    show the full provenance/verification record
  portman export                    write curated facts to mappings/curated.jsonl
  portman import                    load curated facts from mappings/curated.jsonl
  portman init --upstream-root ...  generate a portman.toml for a new port
  portman doctor                    validate the setup before trusting numbers
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
    r = inventory.build_inventory(cfg, db, allow_parse_errors=not args.strict)
    print(f"upstream: {r['upstream_symbols']} symbols @ {cfg.upstream.version or 'working'}")
    print(f"target:   {r['target_symbols']} symbols")
    if r["parse_errors"]:
        print(f"⚠️  parse errors: {r['parse_errors']} (excluded from coverage; "
              f"see `portman doctor`). Use --strict to fail on these.")


def cmd_map(args):
    cfg = _cfg(args); db = _db(cfg)
    r = inventory.auto_map(cfg, db)
    print(f"file pairs: {r['file_pairs']}  (header-confirmed: {r['header_confirmed']})")
    print(f"auto-linked symbols: {r['linked']}  "
          f"(ambiguous/unlinked name-collisions: {r['ambiguous']})")


def cmd_status(args):
    cfg = _cfg(args); db = _db(cfg)
    cov = progress.coverage(db, cfg.upstream.version)
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
    print("  by status:")
    for k, v in sorted(cov["by_status"].items(), key=lambda x: -x[1]):
        print(f"    {k:14} {v}")


def cmd_gaps(args):
    cfg = _cfg(args); db = _db(cfg)
    gp = progress.gaps(db, cfg.upstream.version, limit=args.limit,
                       risk_high=cfg.risk_high, risk_medium=cfg.risk_medium)
    if args.public:
        gp = [g for g in gp if g["public"]]
    for g in gp:
        print(f"[{g['risk']}] {g['path']}::{g['qualname']} ({g['kind']}) {g['status']}")
    print(f"-- {len(gp)} gaps")


def cmd_report(args):
    cfg = _cfg(args); db = _db(cfg)
    cov = reportmod.write_all(db, cfg.upstream.version, cfg.reports_dir,
                              risk_high=cfg.risk_high, risk_medium=cfg.risk_medium)
    print(f"wrote {cfg.reports_dir}/dashboard.md  "
          f"(symbol {cov['symbol_pct']}%, public-API {cov['public_api_pct']}%)")


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
    if db.has_version("upstream", resolved):
        return resolved
    sha = subprocess.run(["git", "-C", str(cfg.upstream.root), "rev-parse", v],
                         capture_output=True, text=True).stdout.strip()
    if sha and db.has_version("upstream", sha):
        return sha
    return resolved   # caller reports the missing snapshot


def cmd_diff(args):
    cfg = _cfg(args); db = _db(cfg)
    old = _resolve_diff_version(cfg, db, args.old)
    new = _resolve_diff_version(cfg, db, args.new)
    for label, raw, res in (("old", args.old, old), ("new", args.new, new)):
        if not db.has_version("upstream", res):
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


def cmd_set(args):
    if args.status == Status.ALIASED.value:   # guard programmatic callers; argparse blocks the CLI
        print("error: use `portman alias A --of B` to create an aliased mapping "
              "(it needs a `covers` target that `set` cannot supply).")
        return 1
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


def _resolve_upstream_sid(db, cfg, spec: str, kind: str):
    """Resolve a symbol spec to a sid. Accepts an exact 'path::Qualname' or a bare
    'Qualname' that is searched within the upstream inventory. Returns
    (sid, error_message)."""
    if "::" in spec:
        path, _, qual = spec.partition("::")
        return symbol_id(cfg.upstream.repo, path, qual, kind), None
    matches = [s for s in db.symbols("upstream", cfg.upstream.version)
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
    cfg = _cfg(args); db = _db(cfg)
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
        status=Status.ALIASED.value, covers=primary_sid, confidence="manual",
        note=args.note or f"alias of {args.of}"))
    db.export_curated(cfg.root / "mappings" / "curated.jsonl")
    print(f"aliased {args.alias} -> covered by {args.of} (target preserved; "
          f"curated.jsonl updated)")


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
    cfg = _cfg(args); db = _db(cfg)
    db.export_curated(cfg.root / "mappings" / "curated.jsonl")
    print("exported curated facts -> mappings/curated.jsonl")


def cmd_import(args):
    """Re-load curated facts from mappings/curated.jsonl into the DB. (Also done
    automatically on every DB open; this is the explicit form.)"""
    cfg = _cfg(args); db = DB(cfg.db_path)
    path = cfg.root / "mappings" / "curated.jsonl"
    db.import_curated(path)
    print(f"imported curated facts <- {path}")


TOML_TEMPLATE = '''\
project = "{project}"
db = "mappings/port.db"
reports = "reports"

[upstream]
repo = "{up_repo}"
root = "{up_root}"
adapter = "{up_adapter}"
version = "{up_version}"
exclude = ["__pycache__"]

[target]
repo = "{tg_repo}"
root = "{tg_root}"
adapter = "{tg_adapter}"
version = "working"
exclude = ["__pycache__"]

# Foundational-path bonuses for gap risk ranking (library-specific, optional).
[risk]
high = []
medium = []
'''


def cmd_init(args):
    out = Path(args.config)
    if out.exists() and not args.force:
        print(f"refusing to overwrite existing {out} (use --force)"); return 1
    out.write_text(TOML_TEMPLATE.format(
        project=args.project, up_repo=args.upstream_repo, up_root=args.upstream_root,
        up_adapter=args.upstream_adapter, up_version=args.upstream_version,
        tg_repo=args.target_repo, tg_root=args.target_root, tg_adapter=args.target_adapter))
    print(f"wrote {out}. Next: portman --config {out} doctor && make all")


def cmd_doctor(args):
    """Validate that the setup is sane before trusting any numbers."""
    cfg = _cfg(args)
    checks: list[tuple[str, str, str]] = []   # (level, name, detail)

    def add(ok, name, detail="", warn=False):
        checks.append(("PASS" if ok else ("WARN" if warn else "FAIL"), name, detail))

    add(cfg.upstream.root.is_dir(), "upstream root exists", str(cfg.upstream.root))
    add(cfg.target.root.is_dir(), "target root exists", str(cfg.target.root))
    # git repo for upstream (needed for `snapshot`)
    git_ok = subprocess.run(["git", "-C", str(cfg.upstream.root), "rev-parse", "--show-toplevel"],
                            capture_output=True).returncode == 0
    add(git_ok, "upstream is a git repo (for snapshot)", "", warn=not git_ok)
    # adapters loadable
    try:
        _adapter_check(cfg); add(True, "adapters loadable",
                                 f"{cfg.upstream.adapter}, {cfg.target.adapter}")
    except Exception as e:
        add(False, "adapters loadable", str(e))
    # reports dir writable
    try:
        cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        add(True, "reports dir writable", str(cfg.reports_dir))
    except Exception as e:
        add(False, "reports dir writable", str(e))
    # curated jsonl valid
    cur = cfg.root / "mappings" / "curated.jsonl"
    add(_curated_valid(cur), "curated.jsonl parses", str(cur))

    # DB-derived checks (only if the DB exists)
    if cfg.db_path.exists():
        db = DB(cfg.db_path)
        pe = db.parse_errors("upstream", cfg.upstream.version) + db.parse_errors("target", cfg.target.version)
        add(not pe, f"no parse errors ({len(pe)})", "; ".join(r["path"] for r in pe[:3]), warn=bool(pe))
        dups = db.duplicate_targets()
        add(not dups, f"no duplicate target mappings ({len(dups)})",
            "run `portman map` after fixes", warn=bool(dups))
        bad_dev = [r for r in db.mappings()
                   if r["status"] == Status.DIVERGED.value and not r["deviation_id"]]
        add(not bad_dev, f"diverged mappings have deviation ids ({len(bad_dev)})")
        # alias integrity: each aliased mapping must name a primary that shares its target
        by_sid = {r["upstream_sid"]: r for r in db.mappings()}
        bad_alias = []
        for r in db.mappings():
            if r["status"] == Status.ALIASED.value:
                prim = by_sid.get(r["covers"])
                if not r["covers"] or not prim or prim["target_sid"] != r["target_sid"]:
                    bad_alias.append(r["upstream_sid"])
        add(not bad_alias, f"aliased mappings reference a valid primary ({len(bad_alias)})",
            "; ".join(bad_alias[:3]))
    else:
        add(True, "database present", "(not built yet — run `portman inventory`)", warn=True)

    fails = sum(1 for lv, *_ in checks if lv == "FAIL")
    for lv, name, detail in checks:
        mark = {"PASS": "✓", "WARN": "•", "FAIL": "✗"}[lv]
        print(f"  {mark} [{lv}] {name}" + (f" — {detail}" if detail else ""))
    print(f"\n{len(checks)} checks, {fails} failing")
    return 1 if fails else 0


def _adapter_check(cfg):
    from .adapters import get_adapter
    get_adapter(cfg.upstream.adapter, cfg.generic_adapters.get(cfg.upstream.adapter))
    get_adapter(cfg.target.adapter, cfg.generic_adapters.get(cfg.target.adapter))


def _curated_valid(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                json.loads(line)
        return True
    except Exception:
        return False


def build_parser():
    p = argparse.ArgumentParser(prog="portman", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="portman.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inventory"); s.add_argument("--strict", action="store_true",
        help="fail if any file fails to parse"); s.set_defaults(func=cmd_inventory)
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
    # `aliased` is deliberately excluded: it requires a `covers` target, which set
    # cannot supply. Use the dedicated `portman alias A --of B` command instead.
    set_statuses = [x.value for x in Status if x is not Status.ALIASED]
    s = sub.add_parser("set", help="set a mapping's status (use `alias` for aliased)")
    s.add_argument("status", choices=set_statuses)
    s.add_argument("--upstream", required=True); s.add_argument("--kind", default="function")
    s.add_argument("--verification", default=""); s.add_argument("--owner", default="")
    s.add_argument("--deviation", default=""); s.add_argument("--note", default=""); s.set_defaults(func=cmd_set)
    s = sub.add_parser("alias"); s.add_argument("alias")
    s.add_argument("--of", required=True, help="primary upstream symbol path::Qualname")
    s.add_argument("--kind", default="method"); s.add_argument("--of-kind", dest="of_kind", default="")
    s.add_argument("--note", default=""); s.set_defaults(func=cmd_alias)
    s = sub.add_parser("trace"); s.add_argument("target"); s.set_defaults(func=cmd_trace)
    sub.add_parser("export").set_defaults(func=cmd_export)
    sub.add_parser("import").set_defaults(func=cmd_import)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    s = sub.add_parser("init")
    s.add_argument("--project", default="myport")
    s.add_argument("--upstream-root", dest="upstream_root", required=True)
    s.add_argument("--target-root", dest="target_root", required=True)
    s.add_argument("--upstream-adapter", dest="upstream_adapter", default="python")
    s.add_argument("--target-adapter", dest="target_adapter", default="rss")
    s.add_argument("--upstream-repo", dest="upstream_repo", default="upstream")
    s.add_argument("--target-repo", dest="target_repo", default="target")
    s.add_argument("--upstream-version", dest="upstream_version", default="HEAD")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
