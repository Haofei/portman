# Changelog

## Unreleased — split the CLI; DRY the read model

- **Refactor: `commands.py`.** The CLI god-file (584 lines mixing command logic
  with parser wiring) split into `commands.py` (the `cmd_*` implementations) and a
  thin `cli.py` (101 lines: docstring + `build_parser` + `main`). New `_ctx(args)`
  helper replaces the `cfg = _cfg(args); db = _db(cfg)` boilerplate at all 13 call
  sites. Pure restructure; behavior unchanged, all commands verified.
- **DRY: `db.mapping_index()`** — one keyed-by-`upstream_sid` lookup, replacing the
  mapping-index comprehension that `progress.coverage`/`gaps` each rebuilt (and a
  redundant `db.mappings()` re-query in the gaps explain path).

## Unreleased — extract the matcher; fix forced-link target collisions

- **Refactor: `matching.py`.** The cross-language matcher (`MappingRules`, name
  normalization, `match_score`, `best_target_candidate`, kind compatibility) moved
  out of `inventory.py` into a dedicated, dependency-light module — the
  language-agnostic engine is now its own unit. `inventory.py` keeps orchestration
  (adapters, `build_inventory`, `file_correspondence`, `auto_map`, symbol links).
  The lazy import-cycle workarounds in `progress.py` are gone (top-level imports).
  Matcher logic is byte-identical; reference numbers unchanged.
- **Fix: forced/human links now claim their target.** A `[mapping.symbol_links]`
  or `portman link`/manual mapping marks its target as taken, so the auto-mapper
  no longer double-assigns it to another upstream symbol (e.g. a forced
  `Device -> device.rss::Device` colliding with auto-mapped `_Device`). Aliases
  are excepted — they intentionally share their primary's target. Regression test
  added; previously this produced a duplicate-target 1:1 violation.

## Unreleased — language-agnostic core (tinygrad demoted to an example)

The matcher previously hardcoded Python source conventions and rsscript signature
syntax in the core. Now language enters only through three seams; tinygrad ↔
rsscript is just the example config. Behavior for the reference port is unchanged
(1695 links / 180 ambiguous / 0 duplicate targets).

- **Signature syntax moved to adapters.** `Adapter.arg_types(signature)` (base
  returns none); the `read|mut|fresh` parser now lives in the rss adapter. The
  core no longer knows any signature syntax.
- **Naming conventions are opt-in config.** `[mapping] owner_qualified`
  (default on, universal), `dunder_passthrough` and `inplace_suffix` (default
  off; the Python conventions). The reference `portman.toml` enables the latter
  two; remove `[mapping]` for a conservative generic matcher.
- **Provenance is extension-driven.** The legacy header regex uses the upstream
  adapter's file extension(s) instead of a hardcoded `.py`.
- `tests/agnostic.py` proves the conventions are off by default and on only when
  configured, and that signature parsing is adapter-owned.
- New `docs/13-language-agnostic.md`; README reframed.

## Unreleased — compiler-inventory ingestion (todo chunk E, #4)

- **`[target] inventory = "inv.json"`** ingests a compiler-produced symbol
  inventory (`module`, `qualname`, `kind`, `visibility`, `source_span`,
  `lowered_name`) instead of scraping `.rss` text. Because qualnames are
  source-level, matching against upstream is exact and the name-bridging
  heuristics stop being load-bearing; `lowered_name` is kept for traceability.
  The regex scraper remains the automatic fallback when the file is absent
  (`inventory build` reports which source was used). This is the cross-repo half:
  rsscript needs to emit the JSON; portman's ingestion side is done + tested.

## Unreleased — batch planning (todo chunk D, #3/#8/#9)

- **`portman batches`** groups related gaps into coherent port batches by
  (upstream file, owner class) — e.g. "ElementwiseMixin methods",
  "OpMixin methods", "UOp methods" — each with its suggested target file, a
  reason histogram, derived blockers, risk, and expected coverage-impact points.
- **Manifest export (#9):** `batches --out FILE` (or `--json`) writes a
  machine-readable worklist: per batch the symbols, target file, blockers,
  coverage impact, and a verification command (`[verify].command`). Agents can
  pick up a batch without re-deriving it.
- **Dependency hints (#8):** `[deps].boost` ranks unlocking symbols first, so
  e.g. the `UOp` batch surfaces near the top.

## Unreleased — forced symbol links (todo chunk C, #1)

- **`[mapping.symbol_links]`** — explicit upstream `path::Qual` -> target
  `path::Qual` links for names the matcher can't bridge (namespace flattening,
  typevars, renames). Re-derived from config each `map` (confidence=`config`,
  locked against the auto-mapper, never written to curated.jsonl). Missing
  endpoints are reported by `map`.
- **`portman link UP TARGET`** — one-off durable forced link (confidence=manual,
  persisted to curated.jsonl), the counterpart to `portman alias`.
- Renamed to avoid collision: "alias" stays the covered-by relation; forced
  name-bridging is "link". `gaps --explain` suggests `link_candidate` where a
  close target name exists.

## Unreleased — port-workflow features (todo chunks A+B)

From `docs/tinygrad-port-todo.md`. A new shared `classify` module unifies several
asks so coverage, gaps, report, and `--explain` agree:

- **Coverage by source area (#5).** `[areas]` config (name -> path prefixes);
  `status`, `report`, and `coverage.json` show per-area done/total/% (e.g. on
  tinygrad: renderer 35.6%, runtime 37.5% … tensor 90.6%).
- **Unified gap reasons (#2).** Every gap is tagged `missing` / `alias_needed` /
  `type_only` / `kind_mismatch` / `already_mapped` / `link_candidate`, plus
  structural `ignored` / `copied_generated` and declared overrides via
  `[gap_reasons]`. `gaps` prints a reason histogram; `--reason R` filters.
- **`gaps --explain` (#11).** Surfaces *why* a symbol isn't linked, including the
  closest in-file target candidate ("target X exists but kind differs",
  "already mapped to Y", "close name — add a forced link").
- **Reasoned ignores (#7) + copied roots (#6).** `[ignore]` (with reasons) and
  `[copied]` segment out-of-scope/generated symbols from the real denominators;
  reported separately, never shown as missing work.
- **Regression guard (#10).** `status --save FILE` and
  `status --fail-on-regression FILE` (exits nonzero if symbol/public-API/verified/
  weighted coverage drops) — a first-class version of the CI floor check.
- **Dependency hints (#8, partial).** `[deps].boost` ranks unlocking symbols first
  (e.g. `UOp.const`/`UOp.alu` now top the gap list).

## Earlier — alias / covered-by mappings

- **New `aliased` status + `portman alias A --of B`.** Lets an upstream symbol be
  intentionally covered by another symbol's target (a private forwarder like
  `Tensor._data`, a public wrapper, or a re-export) **without violating target
  uniqueness**. The duplicate-target check counts only *primary* mappings; aliases
  are excluded. Aliases count as covered (weight 1.0), are dropped from the gap
  list, shown in the dashboard + `trace` ("covered-by"), and validated by `doctor`
  (each alias must name a primary that shares its target). Accepts bare qualnames
  (`Tensor._data`) or `path::Qualname`. Schema + regression test added.
- **export bug fixed:** ambiguous auto-mappings (which carry an auto note) are no
  longer written to `curated.jsonl`; only human-owned facts (manual/review/owner/
  deviation) are exported.
- **`set` no longer exposes `aliased`.** It cannot supply the required `covers`
  target, so allowing it would let users create invalid aliased mappings. `set`'s
  choices exclude it (with a guard for programmatic callers) and point to the
  dedicated `portman alias A --of B`. Regression test added.

## Earlier — mapping-accuracy & version-resolution pass

- **Method-name over-normalization fixed.** Trailing-underscore in-place methods
  (`to_`→`tensor_to_inplace`), leading-underscore privates (`_data` no longer
  steals the public name), and dunders (`__hash__` matches the raw target spelling)
  now resolve; exact target qualname (score 4) beats a normalized tie. Regression
  test `tests/name_matching.py` wired into `make test`.
- **snapshot/diff tag-vs-SHA mismatch fixed.** `snapshot --version <tag>` stores
  symbols under the resolved sha *and* records a `version_aliases` row, so
  `diff <tag> <sha>` (and the CI flow) resolves correctly. A missing snapshot now
  yields a clear error + nonzero exit instead of an empty/misleading diff.
- **Port-specific mapping rules moved to config.** `TARGET_TYPE_ALIASES`,
  `TARGET_OWNER_PREFIX_ALIASES`, and the UOp cache-type heuristic are gone from the
  generic engine; they now live in `portman.toml` `[mapping]` (empty by default).
  Target-side receiver inference is gated to the target side so upstream Python
  annotations can't mint phantom owner forms. Net mapping result unchanged
  (1682 links, 187 ambiguous, 0 duplicate targets).

## Earlier — review-driven correctness & portability pass

Addresses an external source review. The theme: **make it stricter, refuse to
overclaim parity.**

### Correctness (Tier A)
- **Collision-safe auto-mapping (#6).** The matcher previously let several upstream
  methods sharing a leaf name (e.g. `AM_IP.init_hw`, `AM_SOC.init_hw`, …) all claim
  one target `fn init_hw`. Now name forms are split into *strong* (owner-qualified)
  vs *weak* (bare leaf); each target is awarded to a single best upstream claimant,
  and ties are flagged `ambiguous` and **not counted as ported**. Duplicate target
  mappings dropped from 81 (139 redundant links) to **0**; 206 ambiguous links are
  now surfaced for human disambiguation.
- **Separate coverage dimensions (#5).** Public-API coverage now counts only API
  kinds (class/function/method/constant/type); files/modules/tests get their own
  axes. Headline public-API went from an inflated 54.1%/2403 to an honest
  **44.6%/2285**. Parse-error files are excluded from every denominator.
- **Richer signature normalization (#4).** Signatures now include actual default
  value expressions, decorators (`@property`/`@staticmethod`/`@overload`), and
  async-ness — so `f(x=1)`→`f(x=2)` and a dropped `@property` are detected.
- **Parse errors are no longer swallowed (#8).** Recorded in a `parse_errors`
  table, emitted as a distinct `parse_error` kind (never healthy inventory), shown
  by `doctor`; `portman inventory --strict` fails on them.
- **Glob/segment exclude matching (#9).** `exclude=["test"]` no longer drops
  `contest/…`; bare tokens match whole path segments, globs match the full path.
- `make test` no longer hides pytest failures — it only falls back to the smoke
  test when pytest is *absent* (#2).
- Removed dead code in the base adapter; fixed an O(n²) provenance lookup.

### Trust & portability (Tier B)
- New commands: `portman import`, `portman doctor`, `portman init`.
- `portman.toml` uses **relative paths**; added `portman.toml.example`.
- CI template is now runnable: generates config for the checkout paths via
  `portman init`, runs `doctor`, gates on public-API floor, and wires a real
  `make verify` (no silent `|| true` pass).
- Gap **risk patterns moved to config** (`[risk] high/medium`) instead of
  hard-coded module names — the framework is library-agnostic again.
- Stricter `mapping.schema.json`: `additionalProperties:false`, `deviation_id`
  required when `status=diverged`, `target_sid` required when implemented/verified.

### Not yet done (tracked in docs/12)
- Verifier plugin API + verification-evidence storage (Phase 4): the `verification`
  axis is still inert until a differential harness is wired, so Verified stays 0%.
- True dependency-graph ranking and symbol-alias migration across upstream moves.
