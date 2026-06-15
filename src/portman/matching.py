"""Cross-language symbol matching — the language-agnostic heart of portman.

Pure logic with no DB/adapter/IO dependencies: given two `Symbol`-shaped rows and
a set of `MappingRules`, decide whether (and how strongly) they correspond.

Names are reduced to a snake_case *interlingua* so any two languages compare in a
common space. Everything language-specific is opt-in via `MappingRules` (config),
and signature syntax comes from the target adapter (`rules.arg_types`); nothing
here knows Python or rsscript. See docs/13-language-agnostic.md."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .config import Config
from .model import Side

#: kinds that are not matchable symbols (files/modules/tests/parse markers)
CODE_KINDS = ("file", "test", "module", "parse_error")


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
    arg_types: Callable[[str], list[tuple[str, str]]] = _no_args

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
                  side: str = Side.TARGET.value) -> tuple[set[str], set[str]]:
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
    if side == Side.TARGET.value and sym["kind"] == "function" and "." not in sym["qualname"]:
        owner = _first_arg_owner(sym, rules)
        if owner:
            leaf = sym["qualname"].rsplit(".", 1)[-1]
            strong.add(f"{_snake(owner)}_{_snake(leaf)}")
            for alias in rules.type_aliases.get(owner, set()):
                strong.add(f"{_snake(alias)}_{_snake(leaf)}")
        strong |= _receiver_method_forms(sym, rules)
    return strong, weak


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


def match_score(u, t, rules: MappingRules = NO_RULES) -> int:
    """0 = no match, 4 = exact target qualname, 3 = strong/strong, 1 = bare-name."""
    if not _kind_compatible(u["kind"], t["kind"]):
        return 0
    if u["kind"] == "method" and t["kind"] in ("method", "function"):
        owner, leaf = u["qualname"].rsplit(".", 1)
        # An explicit owner-qualified target (`fn Owner.method`) that spells the
        # upstream method verbatim is the strongest possible signal — it must beat
        # any inferred owner form (receiver inference / owner_prefix_aliases) and
        # any normalized collision (e.g. `Owner.reduce` vs `Owner.__reduce__`,
        # which share a snake form). Without this an intentionally hand-written
        # method ties with the flat function it wraps and neither links.
        if t["qualname"] == u["qualname"]:
            return 4
        # target preserves the raw method spelling verbatim (e.g. dunders) — exact
        # name beats any normalized tie. Opt-in (Python convention).
        if rules.dunder_passthrough and t["qualname"] == leaf:
            return 4
        if rules.owner_qualified and t["qualname"] == f"{_snake(owner)}_{_raw_snake(leaf)}":
            return 4
        if (rules.inplace_suffix and leaf.endswith("_") and not leaf.endswith("__")
                and t["qualname"] == f"{_snake(owner)}_{_snake(leaf[:-1])}{rules.inplace_suffix}"):
            return 4
    us, uw = _symbol_forms(u, rules, Side.UPSTREAM.value)
    ts, tw = _symbol_forms(t, rules, Side.TARGET.value)
    if us & ts:
        return 3
    if (us & tw) or (uw & ts) or (uw & tw):
        return 1
    return 0


def best_target_candidate(u, tgt_by_uppath, rules, target_owner: dict) -> dict | None:
    """For an UNPORTED upstream symbol, find the closest in-file target and explain
    why it isn't linked: taken by another upstream, kind-incompatible, or just a
    near name (needs a forced link). Returns None if no candidate at all."""
    best, best_sc = None, 0
    for t in tgt_by_uppath.get(u["path"], []):
        if t["kind"] in CODE_KINDS:
            continue
        sc = match_score(u, t, rules)
        if sc > best_sc:
            best, best_sc = t, sc
    if best:
        return {"qualname": best["qualname"], "kind": best["kind"], "path": best["path"],
                "taken_by": target_owner.get(best["sid"]), "kind_mismatch": False}
    # no kind-compatible match — is there a same-name target of an incompatible kind?
    us, uw = _symbol_forms(u, rules, Side.UPSTREAM.value)
    wanted = us | uw
    for t in tgt_by_uppath.get(u["path"], []):
        if t["kind"] in CODE_KINDS:
            continue
        ts, tw = _symbol_forms(t, rules, Side.TARGET.value)
        if wanted & (ts | tw):
            return {"qualname": t["qualname"], "kind": t["kind"], "path": t["path"],
                    "taken_by": None, "kind_mismatch": True}
    return None
