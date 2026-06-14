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
from pathlib import Path

from .config import Config
from .db import DB
from .adapters import get_adapter
from .model import Mapping, Status, Symbol
from . import provenance as prov


def _adapter(cfg: Config, name: str):
    return get_adapter(name, cfg.generic_adapters.get(name))


def build_inventory(cfg: Config, db: DB, allow_parse_errors: bool = True) -> dict:
    up_ad = _adapter(cfg, cfg.upstream.adapter)
    tg_ad = _adapter(cfg, cfg.target.adapter)
    up = up_ad.extract_tree(cfg.upstream.root, "upstream", cfg.upstream.repo,
                            cfg.upstream.version, cfg.upstream.exclude, allow_parse_errors)
    tg = tg_ad.extract_tree(cfg.target.root, "target", cfg.target.repo,
                            cfg.target.version, cfg.target.exclude, allow_parse_errors)
    db.replace_symbols("upstream", cfg.upstream.version, up)
    db.replace_symbols("target", cfg.target.version, tg)
    db.replace_parse_errors("upstream", cfg.upstream.version, up_ad.parse_errors)
    db.replace_parse_errors("target", cfg.target.version, tg_ad.parse_errors)
    return {"upstream_symbols": len(up), "target_symbols": len(tg),
            "parse_errors": len(up_ad.parse_errors) + len(tg_ad.parse_errors)}


def _snake(s: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s.strip("_")).lower()


def _forms(name: str) -> tuple[set[str], set[str]]:
    """Return (strong, weak) normalized name forms for cross-language matching.

    strong = fully-qualified identity (a free symbol's own name, or a method's
             owner-qualified name like 'am_ip_init_hw'). A strong<->strong match
             is unambiguous.
    weak   = a method's bare leaf name ('init_hw'). Bare-name matches are the
             source of cross-class collisions (every class's `init_hw`), so they
             are scored low and only used when unambiguous.
    """
    leaf = name.rsplit(".", 1)[-1]
    strong = {leaf.lower(), leaf.strip("_").lower(), _snake(leaf)}
    weak: set[str] = set()
    if "." in name:                       # a method: owner-qualified is strong
        owner, m = name.rsplit(".", 1)
        strong.add(f"{_snake(owner)}_{_snake(m)}")
        weak |= {m.strip("_").lower(), _snake(m)}   # bare leaf is weak
        strong -= weak                    # the bare leaf is NOT a strong form
    return {f for f in strong if f}, {f for f in weak if f}


def _match_score(u_qual: str, t_qual: str) -> int:
    """0 = no match, 3 = strong/strong (unambiguous), 1 = bare-name only."""
    us, uw = _forms(u_qual)
    ts, tw = _forms(t_qual)
    if us & ts:
        return 3
    if (us & tw) or (uw & ts) or (uw & tw):
        return 1
    return 0


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


def auto_map(cfg: Config, db: DB) -> dict:
    up_syms = db.symbols("upstream", cfg.upstream.version)
    tg_syms = db.symbols("target", cfg.target.version)
    up_paths = {s["path"] for s in up_syms}

    # file symbols on each side, indexed by path
    up_files = {s["path"]: s for s in up_syms if s["kind"] in ("file", "test", "module")}
    tg_files = {s["path"]: s for s in tg_syms if s["kind"] in ("file", "test", "module")}

    # --- 1. establish file correspondence: target path -> upstream path --------
    tgt_ad = _adapter(cfg, cfg.target.adapter)
    declared: dict[str, prov.Provenance] = {}
    for f in tgt_ad.discover(cfg.target.root):
        p = prov.parse_file(f, cfg.target.root)
        if p.declared:
            declared[p.target_path] = p

    up_by_stem = {_stem(p): p for p in up_files}
    file_corr: dict[str, str] = {}     # target path -> upstream path
    header_confirmed = 0
    for tgt_path in tg_files:
        # header takes precedence; else fall back to stem mirroring
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

    # invert: upstream path -> target symbols in the corresponding file; and the
    # reverse path map so declared provenance is O(1), not an O(n) scan per symbol.
    tgt_by_uppath: dict[str, list] = {}
    for t in tg_syms:
        up_path = file_corr.get(t["path"])
        if up_path:
            tgt_by_uppath.setdefault(up_path, []).append(t)
    up_file_to_tgt_file = {v: tg_files[k]["sid"] for k, v in file_corr.items() if k in tg_files}
    uppath_to_tgtpath = {v: k for k, v in file_corr.items()}

    # --- 2. score every candidate edge, then assign with TARGET UNIQUENESS -----
    # links[u_sid] = (target_sid, score); a target is awarded to its single best
    # upstream claimant. Ties at the top score => ambiguous (linked to nobody).
    best_for_target: dict[str, tuple[int, str]] = {}     # t_sid -> (score, u_sid)
    tie_for_target: dict[str, bool] = {}
    cand_for_upstream: dict[str, list[tuple[int, str]]] = {}
    code_kinds = ("file", "test", "module", "parse_error")
    for u in up_syms:
        if u["kind"] in code_kinds:
            continue
        for t in tgt_by_uppath.get(u["path"], []):
            if t["kind"] in code_kinds:
                continue
            sc = _match_score(u["qualname"], t["qualname"])
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
        existing = db.mapping(u["sid"])
        if existing and existing["confidence"] in ("manual", "review"):
            continue  # never clobber a human decision

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
            "file_pairs": len(file_corr), "header_confirmed": header_confirmed}
