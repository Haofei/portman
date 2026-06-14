# Changelog

## Unreleased — review-driven correctness & portability pass

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
