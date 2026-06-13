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


def build_inventory(cfg: Config, db: DB) -> dict:
    up_ad = _adapter(cfg, cfg.upstream.adapter)
    tg_ad = _adapter(cfg, cfg.target.adapter)
    up = up_ad.extract_tree(cfg.upstream.root, "upstream", cfg.upstream.repo,
                            cfg.upstream.version, cfg.upstream.exclude)
    tg = tg_ad.extract_tree(cfg.target.root, "target", cfg.target.repo,
                            cfg.target.version, cfg.target.exclude)
    db.replace_symbols("upstream", cfg.upstream.version, up)
    db.replace_symbols("target", cfg.target.version, tg)
    return {"upstream_symbols": len(up), "target_symbols": len(tg)}


def _norm(name: str) -> set[str]:
    """Candidate normalized forms for cross-language name matching."""
    leaf = name.rsplit(".", 1)[-1]
    base = leaf.strip("_")
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()
    forms = {leaf.lower(), base.lower(), snake}
    # method Foo.bar -> foo_bar / bar  (target often flattens with a type prefix)
    if "." in name:
        owner, m = name.rsplit(".", 1)
        owner_s = re.sub(r"(?<!^)(?=[A-Z])", "_", owner).lower()
        forms |= {f"{owner_s}_{m.strip('_').lower()}", m.strip("_").lower()}
    return {f for f in forms if f}


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

    # invert: upstream path -> list of target symbols in the corresponding file
    tgt_by_uppath: dict[str, list] = {}
    for t in tg_syms:
        up_path = file_corr.get(t["path"])
        if up_path:
            tgt_by_uppath.setdefault(up_path, []).append(t)
    up_file_to_tgt_file = {v: tg_files[k]["sid"] for k, v in file_corr.items() if k in tg_files}

    # --- 2. write mappings -----------------------------------------------------
    linked = proposed = 0
    for u in up_syms:
        existing = db.mapping(u["sid"])
        if existing and existing["confidence"] in ("manual", "review"):
            continue  # never clobber a human decision

        target_sid = None
        if u["kind"] in ("file", "test", "module"):
            target_sid = up_file_to_tgt_file.get(u["path"])
        else:
            wanted = _norm(u["qualname"])
            for t in tgt_by_uppath.get(u["path"], []):
                if t["kind"] in ("file", "test", "module"):
                    continue
                if _norm(t["qualname"]) & wanted:
                    target_sid = t["sid"]; break

        status = Status.IMPLEMENTED.value if target_sid else Status.NOT_STARTED.value
        if target_sid:
            proposed += 1; linked += 1
        m = Mapping(upstream_sid=u["sid"], target_sid=target_sid, status=status,
                    declared_upstream_path=(declared.get(_inv(file_corr, u["path"]), prov.Provenance("")).upstream_path
                                            if u["path"] in file_corr.values() else ""),
                    confidence="auto",
                    updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        if existing and existing["owner"]:
            m.owner = existing["owner"]
        db.upsert_mapping(m)
    return {"linked": linked, "proposed": proposed,
            "file_pairs": len(file_corr), "header_confirmed": header_confirmed}


def _inv(d: dict, value: str) -> str:
    for k, v in d.items():
        if v == value:
            return k
    return ""
