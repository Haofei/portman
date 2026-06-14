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

import time
from pathlib import Path

from .config import Config
from .db import DB
from .adapters import get_adapter
from .model import Mapping, Status
from . import provenance as prov
from .matching import MappingRules, CODE_KINDS, match_score, _no_args


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


def build_rules(cfg: Config) -> MappingRules:
    """Config rules + the target adapter's signature parser (for receiver
    inference). Use this everywhere matching happens."""
    rules = MappingRules.from_config(cfg)
    rules.arg_types = getattr(target_adapter(cfg), "arg_types", _no_args)
    return rules


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
    # Targets already claimed by a forced/human link must not be auto-assigned to
    # another upstream (that would create a 1:1 duplicate, e.g. a `symbol_links`
    # Device -> device.rss::Device colliding with auto-mapped _Device). Aliases are
    # excluded: they intentionally SHARE a target with their primary.
    taken_targets = {m["target_sid"] for m in db.mappings()
                     if m["target_sid"] and m["confidence"] in ("manual", "review", "config")
                     and m["status"] != Status.ALIASED.value}

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
            if t["kind"] in CODE_KINDS or t["sid"] in taken_targets:
                continue
            sc = match_score(u, t, rules)
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
