"""portman command implementations — the cmd_* functions behind the CLI.

Each takes parsed argparse args and returns an int exit code (or None == 0).
`cli.py` only wires the argument parser to these; all logic lives here."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .config import Config
from .db import DB
from . import inventory, progress, diff as diffmod, report as reportmod
from . import provenance as prov
from .model import Mapping, symbol_id, Status, Confidence, Side


def _cfg(args) -> Config:
    return Config.load(Path(args.config))


def _db(cfg: Config) -> DB:
    db = DB(cfg.db_path)
    db.import_curated(cfg.root / "mappings" / "curated.jsonl")
    return db


def _ctx(args) -> tuple[Config, DB]:
    """Load config + open the DB (with curated facts imported) — the common
    preamble for nearly every command."""
    cfg = _cfg(args)
    return cfg, _db(cfg)


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
    from .adapters import get_adapter
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
            from .adapters import get_adapter
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
        pe = db.parse_errors(Side.UPSTREAM.value, cfg.upstream.version) + db.parse_errors(Side.TARGET.value, cfg.target.version)
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
