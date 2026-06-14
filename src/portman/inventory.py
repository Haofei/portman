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


def _raw_snake(s: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()


@dataclass
class MappingRules:
    """Project-specific naming conventions for cross-language matching. Empty by
    default so the engine is library-agnostic; values come from portman.toml
    `[mapping]`. See docs/02 for the schema."""
    # target type/struct name (or inferred receiver type) -> upstream type names
    type_aliases: dict[str, set[str]] = field(default_factory=dict)
    # upstream owner class -> target qualname prefixes that flatten its methods
    owner_prefix_aliases: dict[str, list[str]] = field(default_factory=dict)
    # interned-cache receiver type -> {owner, strip_prefix}: a flat target function
    # whose first param is this type and second is (id, Int) is really a method of
    # `owner`, after stripping `strip_prefix` from its name.
    receiver_methods: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: Config) -> "MappingRules":
        m = getattr(cfg, "mapping", {}) or {}
        return cls(
            type_aliases={k: set(v) for k, v in m.get("type_aliases", {}).items()},
            owner_prefix_aliases={k: list(v) for k, v in m.get("owner_prefix_aliases", {}).items()},
            receiver_methods=dict(m.get("receiver_methods", {})))


NO_RULES = MappingRules()


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
        strong.add(f"{_snake(owner)}_{_raw_snake(m)}")
        if m.endswith("_") and not m.endswith("__"):
            strong.add(f"{_snake(owner)}_{_snake(m[:-1])}_inplace")
        weak |= {m.strip("_").lower(), _snake(m)}   # bare leaf is weak
        strong -= weak                    # the bare leaf is NOT a strong form
    return {f for f in strong if f}, {f for f in weak if f}


def _rss_first_arg_type(signature: str) -> str:
    """Best-effort owner inference for flat RSS helper methods.

    The target port often represents a Python method as a free function whose
    first parameter is the receiver, for example `vec(d: read DType, sz: Int)`.
    Treat that as having an additional strong form `DType.vec` while preserving
    the actual target qualname for traceability.
    """
    inner = signature.strip()
    if not inner.startswith("("):
        return ""
    inner = inner[1:].split(")", 1)[0].strip()
    if not inner:
        return ""
    first_param = inner.split(",", 1)[0]
    if ":" not in first_param:
        return ""
    first = first_param.split(":", 1)[1].strip()
    first = re.sub(r"^(read|mut|fresh)\s+", "", first)
    first = first.split("<", 1)[0].strip()
    return first if re.match(r"^[A-Z][A-Za-z0-9_]*$", first) else ""


def _rss_arg_types(signature: str) -> list[tuple[str, str]]:
    inner = signature.strip()
    if not inner.startswith("("):
        return []
    inner = inner[1:].split(")", 1)[0].strip()
    if not inner:
        return []
    out: list[tuple[str, str]] = []
    for param in inner.split(","):
        if ":" not in param:
            continue
        name, ty = param.split(":", 1)
        ty = re.sub(r"^(read|mut|fresh)\s+", "", ty.strip())
        out.append((name.strip(), ty.split("<", 1)[0].strip()))
    return out


def _receiver_method_forms(sym, rules: MappingRules) -> set[str]:
    """Owner-qualified strong forms for a flat target function that is really a
    method. A function is treated as a method of `owner` when EITHER its name
    carries the configured `strip_prefix` (e.g. `uop_foo`) OR its first param is
    the interned-cache receiver type and its second is the id (e.g.
    `foo(c: UOpCache, id: Int, ...)`). Config-driven via [mapping].receiver_methods."""
    if not rules.receiver_methods or sym["kind"] != "function":
        return set()
    args = _rss_arg_types(sym["signature"] or "")
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
    strong, weak = _forms(sym["qualname"])
    # type aliases apply on either side (keyed by the specific name).
    for alias in rules.type_aliases.get(sym["qualname"], set()):
        alias_strong, alias_weak = _forms(alias)
        strong |= alias_strong
        weak |= alias_weak
    for owner, prefixes in rules.owner_prefix_aliases.items():
        for prefix in prefixes:
            if sym["qualname"].startswith(f"{prefix}_"):
                leaf = sym["qualname"][len(prefix) + 1:]
                strong.add(f"{_snake(owner)}_{_snake(leaf)}")
    # First-arg receiver inference is a TARGET-side convention (the port flattens
    # a method into a free function whose first param is the receiver). Applying
    # it to upstream would mint phantom owner forms from Python type annotations.
    if side == "target" and sym["kind"] == "function" and "." not in sym["qualname"]:
        owner = _rss_first_arg_type(sym["signature"] or "")
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
        owner_leaf = f"{_snake(owner)}_{_raw_snake(leaf)}"
        # raw method spelling preserved verbatim by the target (e.g. dunders
        # `__hash__`, `__eq__`): the exact name beats any normalized tie. This
        # stops `Tensor.hash` and `Tensor.__hash__` from contending for it.
        if t["qualname"] == leaf:
            return 4
        if t["qualname"] == owner_leaf:
            return 4
        if leaf.endswith("_") and not leaf.endswith("__") and t["qualname"] == f"{_snake(owner)}_{_snake(leaf[:-1])}_inplace":
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
    return {
        "up_syms": up_syms, "tg_syms": tg_syms, "declared": declared,
        "file_corr": file_corr, "tgt_by_uppath": tgt_by_uppath,
        "header_confirmed": header_confirmed,
        "up_file_to_tgt_file": {v: tg_files[k]["sid"] for k, v in file_corr.items() if k in tg_files},
        "uppath_to_tgtpath": {v: k for k, v in file_corr.items()},
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
        return {"qualname": best["qualname"], "kind": best["kind"],
                "taken_by": target_owner.get(best["sid"]), "kind_mismatch": False}
    # no kind-compatible match — is there a same-name target of an incompatible kind?
    us, uw = _symbol_forms(u, rules, "upstream")
    wanted = us | uw
    for t in tgt_by_uppath.get(u["path"], []):
        if t["kind"] in CODE_KINDS:
            continue
        ts, tw = _symbol_forms(t, rules, "target")
        if wanted & (ts | tw):
            return {"qualname": t["qualname"], "kind": t["kind"],
                    "taken_by": None, "kind_mismatch": True}
    return None


def auto_map(cfg: Config, db: DB) -> dict:
    rules = MappingRules.from_config(cfg)
    fc = file_correspondence(cfg, db)
    up_syms, tg_syms = fc["up_syms"], fc["tg_syms"]
    declared = fc["declared"]
    file_corr, tgt_by_uppath = fc["file_corr"], fc["tgt_by_uppath"]
    up_file_to_tgt_file, uppath_to_tgtpath = fc["up_file_to_tgt_file"], fc["uppath_to_tgtpath"]
    header_confirmed = fc["header_confirmed"]

    # Human-owned mappings (manual/review/ambiguous-resolved/aliased) are locked:
    # they neither compete for targets nor get clobbered, so an explicit alias
    # (e.g. _data -> tensor_data) doesn't re-contend with its primary (data).
    locked = {m["upstream_sid"] for m in db.mappings()
              if m["confidence"] in ("manual", "review") or m["status"] == Status.ALIASED.value}

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
            "file_pairs": len(file_corr), "header_confirmed": header_confirmed}
