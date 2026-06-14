"""Inventory + auto-mapping.

`build_inventory` extracts both sides into the DB. `auto_map` proposes links:

  1. File links come from declared provenance headers (strong signal).
  2. Within a linked file pair, symbols are matched leaf-name to leaf-name using
     a normalization that tolerates the upstream->target naming convention
     (e.g. Python `Tensor.reshape` -> rss `tensor_reshape`).

Auto links are written with confidence="auto" and never overwrite a human
"manual"/"review" decision. A status is *proposed* (implemented) only when a
target symbol is found; humans promote to verified/diverged."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .db import DB
from .adapters import get_adapter
from .model import Mapping, Status, Symbol
from . import provenance as prov


def _adapter(cfg: Config, name: str):
    return get_adapter(name, cfg.generic_adapters.get(name))


def target_adapter(cfg: Config):
    """Prefer a compiler-produced JSON inventory (#4) for the target when present;
    otherwise fall back to the configured source scraper."""
    inv = cfg.target.inventory
    if inv and Path(inv).exists():
        from .adapters.inventory_json import JsonInventoryAdapter
        return JsonInventoryAdapter(inv)
    return _adapter(cfg, cfg.target.adapter)


def _upstream_exts(cfg: Config) -> tuple[str, ...]:
    """Upstream file extensions (from the upstream adapter's globs), so provenance
    header parsing is not Python-specific. '*.py' -> ('py',)."""
    pats = _adapter(cfg, cfg.upstream.adapter).patterns
    return tuple(p.rsplit(".", 1)[-1] for p in pats) or ("py",)


def build_inventory(cfg: Config, db: DB, allow_parse_errors: bool = True) -> dict:
    up_ad = _adapter(cfg, cfg.upstream.adapter)
    tg_ad = target_adapter(cfg)
    up = up_ad.extract_tree(cfg.upstream.root, "upstream", cfg.upstream.repo,
                            cfg.upstream.version, cfg.upstream.exclude, allow_parse_errors)
    tg = tg_ad.extract_tree(cfg.target.root, "target", cfg.target.repo,
                            cfg.target.version, cfg.target.exclude, allow_parse_errors)
    db.replace_symbols("upstream", cfg.upstream.version, up)
    db.replace_symbols("target", cfg.target.version, tg)
    db.replace_parse_errors("upstream", cfg.upstream.version, up_ad.parse_errors)
    db.replace_parse_errors("target", cfg.target.version, tg_ad.parse_errors)
    return {"upstream_symbols": len(up), "target_symbols": len(tg),
            "parse_errors": len(up_ad.parse_errors) + len(tg_ad.parse_errors),
            "target_source": "inventory" if getattr(tg_ad, "name", "") == "inventory" else "scraper"}


def _snake(s: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s.strip("_")).lower()


def _raw_snake(s: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()


def _no_args(signature: str) -> list[tuple[str, str]]:
    return []


@dataclass
class MappingRules:
    """Cross-language matching conventions. Everything is generic with safe
    defaults; the Python/rsscript-specific behaviour is OPT-IN via portman.toml
    `[mapping]` (so tinygrad is just an example, not baked into the core).

    Universal (on by default):
      owner_qualified  — a method `Owner.m` also matches a flattened `owner_m`.
    Opt-in conventions:
      dunder_passthrough — a method whose leaf the target preserves verbatim
                           (e.g. Python dunders `__hash__`) matches that exact name.
      inplace_suffix     — map a source in-place spelling `Owner.m_` to
                           `owner_m<suffix>` (e.g. `_inplace`); "" disables.
    Project data:
      type_aliases / owner_prefix_aliases / receiver_methods (see docs/02).
    Adapter-provided:
      arg_types(signature) — the TARGET adapter's signature parser, used for
                           receiver inference. Defaults to none (no parsing)."""
    type_aliases: dict[str, set[str]] = field(default_factory=dict)
    owner_prefix_aliases: dict[str, list[str]] = field(default_factory=dict)
    receiver_methods: dict[str, dict] = field(default_factory=dict)
    owner_qualified: bool = True
    dunder_passthrough: bool = False
    inplace_suffix: str = ""
    arg_types: object = _no_args          # callable: signature -> [(name, type)]

    @classmethod
    def from_config(cls, cfg: Config) -> "MappingRules":
        m = getattr(cfg, "mapping", {}) or {}
        return cls(
            type_aliases={k: set(v) for k, v in m.get("type_aliases", {}).items()},
            owner_prefix_aliases={k: list(v) for k, v in m.get("owner_prefix_aliases", {}).items()},
            receiver_methods=dict(m.get("receiver_methods", {})),
            owner_qualified=bool(m.get("owner_qualified", True)),
            dunder_passthrough=bool(m.get("dunder_passthrough", False)),
            inplace_suffix=str(m.get("inplace_suffix", "")))


NO_RULES = MappingRules()


def build_rules(cfg: Config) -> "MappingRules":
    """Config rules + the target adapter's signature parser (for receiver
    inference). Use this everywhere matching happens."""
    rules = MappingRules.from_config(cfg)
    rules.arg_types = getattr(target_adapter(cfg), "arg_types", _no_args)
    return rules


def _forms(name: str, rules: MappingRules = NO_RULES) -> tuple[set[str], set[str]]:
    """Return (strong, weak) normalized name forms. Names are reduced to a
    snake_case interlingua so any two languages compare in a common space.

    strong = fully-qualified identity (a free symbol's own name, or, when
             owner_qualified, a method's owner-joined name like 'am_ip_init_hw').
    weak   = a method's bare leaf name ('init_hw'); bare matches are scored low
             because every class's `init_hw` collides on it."""
    leaf = name.rsplit(".", 1)[-1]
    strong = {leaf.lower(), leaf.strip("_").lower(), _snake(leaf)}
    weak: set[str] = set()
    if "." in name:                       # a method
        owner, m = name.rsplit(".", 1)
        if rules.owner_qualified:
            strong.add(f"{_snake(owner)}_{_snake(m)}")
            strong.add(f"{_snake(owner)}_{_raw_snake(m)}")
            if rules.inplace_suffix and m.endswith("_") and not m.endswith("__"):
                strong.add(f"{_snake(owner)}_{_snake(m[:-1])}{rules.inplace_suffix}")
        weak |= {m.strip("_").lower(), _snake(m)}   # bare leaf is weak
        strong -= weak                    # the bare leaf is NOT a strong form
    return {f for f in strong if f}, {f for f in weak if f}


def _first_arg_owner(sym, rules: MappingRules) -> str:
    """Receiver type of a flat target function, via the target adapter's signature
    parser. e.g. `vec(d: DType, sz: Int)` -> 'DType'. Capitalized types only."""
    args = rules.arg_types(sym["signature"] or "")
    if args and re.match(r"^[A-Z][A-Za-z0-9_]*$", args[0][1] or ""):
        return args[0][1]
    return ""


def _receiver_method_forms(sym, rules: MappingRules) -> set[str]:
    """Owner-qualified strong forms for a flat target function that is really a
    method — when its name carries a configured `strip_prefix` OR its first param
    is a configured interned-cache receiver type with the id as second param.
    Config-driven via [mapping].receiver_methods; the signature parse is the
    target adapter's."""
    if not rules.receiver_methods or sym["kind"] != "function":
        return set()
    args = rules.arg_types(sym["signature"] or "")
    out: set[str] = set()
    for cache_type, spec in rules.receiver_methods.items():
        owner, sp = spec["owner"], spec.get("strip_prefix", "")
        name_match = bool(sp) and sym["qualname"].startswith(sp)
        sig_match = len(args) >= 2 and args[0][1] == cache_type and args[1] == ("id", "Int")
        if name_match or sig_match:
            leaf = sym["qualname"]
            if sp and leaf.startswith(sp):
                leaf = leaf[len(sp):]
            out.add(f"{_snake(owner)}_{_snake(leaf)}")
    return out


def _symbol_forms(sym, rules: MappingRules = NO_RULES,
                  side: str = "target") -> tuple[set[str], set[str]]:
    strong, weak = _forms(sym["qualname"], rules)
    for alias in rules.type_aliases.get(sym["qualname"], set()):
        a_s, a_w = _forms(alias, rules)
        strong |= a_s; weak |= a_w
    for owner, prefixes in rules.owner_prefix_aliases.items():
        for prefix in prefixes:
            if sym["qualname"].startswith(f"{prefix}_"):
                leaf = sym["qualname"][len(prefix) + 1:]
                strong.add(f"{_snake(owner)}_{_snake(leaf)}")
    # Receiver inference is a TARGET-side convention (flattened method whose first
    # param is the receiver). Applying it upstream would mint phantom owner forms.
    if side == "target" and sym["kind"] == "function" and "." not in sym["qualname"]:
        owner = _first_arg_owner(sym, rules)
        if owner:
            leaf = sym["qualname"].rsplit(".", 1)[-1]
            strong.add(f"{_snake(owner)}_{_snake(leaf)}")
            for alias in rules.type_aliases.get(owner, set()):
                strong.add(f"{_snake(alias)}_{_snake(leaf)}")
        strong |= _receiver_method_forms(sym, rules)
    return strong, weak


def _match_score(u, t, rules: MappingRules = NO_RULES) -> int:
    """0 = no match, 4 = exact target qualname, 3 = strong/strong, 1 = bare-name."""
    if not _kind_compatible(u["kind"], t["kind"]):
        return 0
    if u["kind"] == "method" and t["kind"] in ("method", "function"):
        owner, leaf = u["qualname"].rsplit(".", 1)
        # target preserves the raw method spelling verbatim (e.g. dunders) — exact
        # name beats any normalized tie. Opt-in (Python convention).
        if rules.dunder_passthrough and t["qualname"] == leaf:
            return 4
        if rules.owner_qualified and t["qualname"] == f"{_snake(owner)}_{_raw_snake(leaf)}":
            return 4
        if (rules.inplace_suffix and leaf.endswith("_") and not leaf.endswith("__")
                and t["qualname"] == f"{_snake(owner)}_{_snake(leaf[:-1])}{rules.inplace_suffix}"):
            return 4
    us, uw = _symbol_forms(u, rules, "upstream")
    ts, tw = _symbol_forms(t, rules, "target")
    if us & ts:
        return 3
    if (us & tw) or (uw & ts) or (uw & tw):
        return 1
    return 0


def _kind_compatible(upstream_kind: str, target_kind: str) -> bool:
    if upstream_kind == "class":
        return target_kind in ("class", "type")
    if upstream_kind == "type":
        return target_kind in ("type", "class")
    if upstream_kind == "method":
        return target_kind in ("method", "function")
    if upstream_kind == "function":
        return target_kind in ("function", "method")
    if upstream_kind == "constant":
        return target_kind == "constant"
    return upstream_kind == target_kind


def _stem(path: str) -> str:
    """Path without its file extension — the language-agnostic file identity used
    by a path-mirroring 1:1 port (e.g. 'uop/ops.rss' and 'uop/ops.py' share the
    stem 'uop/ops')."""
    return path.rsplit(".", 1)[0] if "." in path.rsplit("/", 1)[-1] else path


def _resolve_declared(declared: str, up_paths: set[str]) -> str:
    """Match a header-declared upstream path against real upstream paths,
    tolerating a leading package segment (header says 'tinygrad/dtype.py', the
    inventory key is 'dtype.py')."""
    if declared in up_paths:
        return declared
    parts = declared.split("/")
    for i in range(1, len(parts)):
        cand = "/".join(parts[i:])
        if cand in up_paths:
            return cand
    return ""


CODE_KINDS = ("file", "test", "module", "parse_error")


def file_correspondence(cfg: Config, db: DB) -> dict:
    """Establish which target file implements which upstream file (provenance
    header first, path-stem mirroring as fallback) and index target symbols by the
    upstream path of their file. Shared by auto_map and `gaps --explain`."""
    up_syms = db.symbols("upstream", cfg.upstream.version)
    tg_syms = db.symbols("target", cfg.target.version)
    up_paths = {s["path"] for s in up_syms}
    up_files = {s["path"]: s for s in up_syms if s["kind"] in ("file", "test", "module")}
    tg_files = {s["path"]: s for s in tg_syms if s["kind"] in ("file", "test", "module")}

    # provenance headers come from source files; a JSON inventory carries none, so
    # correspondence then relies on path-stem mirroring.
    tgt_ad = target_adapter(cfg)
    exts = _upstream_exts(cfg)
    declared: dict[str, prov.Provenance] = {}
    if getattr(tgt_ad, "name", "") != "inventory":
        for f in tgt_ad.discover(cfg.target.root):
            p = prov.parse_file(f, cfg.target.root, exts)
            if p.declared:
                declared[p.target_path] = p

    up_by_stem = {_stem(p): p for p in up_files}
    file_corr: dict[str, str] = {}     # target path -> upstream path
    header_confirmed = 0
    for tgt_path in tg_files:
        up_path = ""
        p = declared.get(tgt_path)
        if p and p.upstream_path:
            up_path = _resolve_declared(p.upstream_path, up_paths)
            if up_path:
                header_confirmed += 1
        if not up_path:
            up_path = up_by_stem.get(_stem(tgt_path), "")
        if up_path:
            file_corr[tgt_path] = up_path

    tgt_by_uppath: dict[str, list] = {}
    for t in tg_syms:
        up_path = file_corr.get(t["path"])
        if up_path:
            tgt_by_uppath.setdefault(up_path, []).append(t)

    # One upstream file may be split across SEVERAL target files (e.g. uop/ops.py ->
    # ops.rss + alu.rss + vminmax.rss). Keep the full list; the "primary" (for the
    # single-target file mapping) is the stem-mirroring target, else the first.
    uppath_to_tgtpaths: dict[str, list[str]] = {}
    for tgt_path, up_path in file_corr.items():
        uppath_to_tgtpaths.setdefault(up_path, []).append(tgt_path)
    for paths in uppath_to_tgtpaths.values():
        paths.sort()

    def _primary(up_path: str) -> str:
        paths = uppath_to_tgtpaths[up_path]
        return next((p for p in paths if _stem(p) == _stem(up_path)), paths[0])

    return {
        "up_syms": up_syms, "tg_syms": tg_syms, "declared": declared,
        "file_corr": file_corr, "tgt_by_uppath": tgt_by_uppath,
        "header_confirmed": header_confirmed,
        "up_file_to_tgt_file": {up: tg_files[_primary(up)]["sid"] for up in uppath_to_tgtpaths},
        "uppath_to_tgtpath": {up: _primary(up) for up in uppath_to_tgtpaths},
        "uppath_to_tgtpaths": uppath_to_tgtpaths,
    }


def best_target_candidate(u, tgt_by_uppath, rules, target_owner: dict) -> dict | None:
    """For an UNPORTED upstream symbol, find the closest in-file target and explain
    why it isn't linked: taken by another upstream, kind-incompatible, or just a
    near name (needs a forced link). Returns None if no candidate at all."""
    best, best_sc = None, 0
    for t in tgt_by_uppath.get(u["path"], []):
        if t["kind"] in CODE_KINDS:
            continue
        sc = _match_score(u, t, rules)
        if sc > best_sc:
            best, best_sc = t, sc
    if best:
        return {"qualname": best["qualname"], "kind": best["kind"], "path": best["path"],
                "taken_by": target_owner.get(best["sid"]), "kind_mismatch": False}
    # no kind-compatible match — is there a same-name target of an incompatible kind?
    us, uw = _symbol_forms(u, rules, "upstream")
    wanted = us | uw
    for t in tgt_by_uppath.get(u["path"], []):
        if t["kind"] in CODE_KINDS:
            continue
        ts, tw = _symbol_forms(t, rules, "target")
        if wanted & (ts | tw):
            return {"qualname": t["qualname"], "kind": t["kind"], "path": t["path"],
                    "taken_by": None, "kind_mismatch": True}
    return None


def apply_symbol_links(cfg: Config, db: DB, up_syms, tg_syms) -> dict:
    """Honor [mapping.symbol_links] — explicit upstream `path::Qual` -> target
    `path::Qual` links for names the matcher can't bridge (namespace flattening,
    typevars, renames). Stored with confidence='config' so they are locked against
    the auto-mapper but NOT written to curated.jsonl (they are re-derived from
    config each run). Returns {linked, missing:[...]}."""
    links = (cfg.mapping or {}).get("symbol_links", {})
    db.clear_config_links()
    up_idx = {(s["path"], s["qualname"]): s for s in up_syms}
    tg_idx = {(s["path"], s["qualname"]): s for s in tg_syms}
    linked, missing = 0, []
    for up_spec, tg_spec in links.items():
        up_p, _, up_q = up_spec.partition("::")
        tg_p, _, tg_q = tg_spec.partition("::")
        u, t = up_idx.get((up_p, up_q)), tg_idx.get((tg_p, tg_q))
        if not u:
            missing.append(f"upstream {up_spec}"); continue
        if not t:
            missing.append(f"target {tg_spec}"); continue
        db.upsert_mapping(Mapping(
            upstream_sid=u["sid"], target_sid=t["sid"], status=Status.IMPLEMENTED.value,
            confidence="config", note=f"forced symbol link: {tg_spec}",
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        linked += 1
    return {"linked": linked, "missing": missing}


def auto_map(cfg: Config, db: DB) -> dict:
    rules = build_rules(cfg)
    fc = file_correspondence(cfg, db)
    up_syms, tg_syms = fc["up_syms"], fc["tg_syms"]
    forced = apply_symbol_links(cfg, db, up_syms, tg_syms)
    declared = fc["declared"]
    file_corr, tgt_by_uppath = fc["file_corr"], fc["tgt_by_uppath"]
    up_file_to_tgt_file, uppath_to_tgtpath = fc["up_file_to_tgt_file"], fc["uppath_to_tgtpath"]
    header_confirmed = fc["header_confirmed"]

    # Human-owned mappings (manual/review/ambiguous-resolved/aliased) are locked:
    # they neither compete for targets nor get clobbered, so an explicit alias
    # (e.g. _data -> tensor_data) doesn't re-contend with its primary (data).
    locked = {m["upstream_sid"] for m in db.mappings()
              if m["confidence"] in ("manual", "review", "config")
              or m["status"] == Status.ALIASED.value}

    # --- 2. score every candidate edge, then assign with TARGET UNIQUENESS -----
    # links[u_sid] = (target_sid, score); a target is awarded to its single best
    # upstream claimant. Ties at the top score => ambiguous (linked to nobody).
    best_for_target: dict[str, tuple[int, str]] = {}     # t_sid -> (score, u_sid)
    tie_for_target: dict[str, bool] = {}
    cand_for_upstream: dict[str, list[tuple[int, str]]] = {}
    for u in up_syms:
        if u["kind"] in CODE_KINDS or u["sid"] in locked:
            continue
        for t in tgt_by_uppath.get(u["path"], []):
            if t["kind"] in CODE_KINDS:
                continue
            sc = _match_score(u, t, rules)
            if not sc:
                continue
            cand_for_upstream.setdefault(u["sid"], []).append((sc, t["sid"]))
            cur = best_for_target.get(t["sid"])
            if cur is None or sc > cur[0]:
                best_for_target[t["sid"]] = (sc, u["sid"]); tie_for_target[t["sid"]] = False
            elif sc == cur[0] and u["sid"] != cur[1]:
                tie_for_target[t["sid"]] = True

    # --- 3. write mappings -----------------------------------------------------
    linked = ambiguous = 0
    for u in up_syms:
        if u["sid"] in locked:
            continue  # never clobber a human decision (manual/review/aliased)
        existing = db.mapping(u["sid"])

        target_sid, status, confidence, note = None, Status.NOT_STARTED.value, "auto", ""
        if u["kind"] in ("file", "test", "module"):
            target_sid = up_file_to_tgt_file.get(u["path"])
            if target_sid:
                status = Status.IMPLEMENTED.value
        else:
            # pick this upstream's best target where it is the UNIQUE top claimant
            cands = sorted(cand_for_upstream.get(u["sid"], []), reverse=True)
            won = next((t for sc, t in cands
                        if best_for_target.get(t, (0, ""))[1] == u["sid"]
                        and not tie_for_target.get(t, False)), None)
            if won:
                target_sid, status = won, Status.IMPLEMENTED.value
            elif cands:                  # had candidates but none uniquely ours
                confidence, note = "ambiguous", "name-collision; needs manual disambiguation"

        dec_tgt = uppath_to_tgtpath.get(u["path"], "")
        m = Mapping(upstream_sid=u["sid"], target_sid=target_sid, status=status,
                    declared_upstream_path=declared.get(dec_tgt, prov.Provenance("")).upstream_path,
                    confidence=confidence, note=note,
                    updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        if existing and existing["owner"]:
            m.owner = existing["owner"]
        db.upsert_mapping(m)
        if target_sid:
            linked += 1
        elif confidence == "ambiguous":
            ambiguous += 1
    return {"linked": linked, "ambiguous": ambiguous,
            "file_pairs": len(file_corr), "header_confirmed": header_confirmed,
            "forced_links": forced["linked"], "forced_missing": forced["missing"]}
