# Feature request: prefer an explicit `fn Type.method` over an inferred receiver-method

**Status:** requested · **Driver:** tinygrad-rsmc UPat method surface
**Repro built on:** current portman (post rss-adapter `module` awareness)

## Summary

When a target provides BOTH an explicit `fn Type.method(self: …)` and a flat
function that the matcher *infers* as `Type.method` (via `owner_prefix_aliases`
or first-arg receiver inference), the two compete for the same upstream method and
the mapping is flagged `ambiguous`. The explicit method should win.

## Motivation / impact

Adding `fn UPat.{after,end,index,reduce,sink}` wrappers closed 4 of 5 alias-needed
`UPat.*` gaps. The fifth, `UPat.reduce`, stayed `not_started / confidence=ambiguous`
because the flat `upat_reduce` is *also* inferred as `UPat.reduce` (the `upat`
owner-prefix alias), so upstream `UPat.reduce` saw two equally-scored target
claimants and matched neither. The other four had `2`-suffixed flat names
(`upat_after2`, …) so no clash arose — i.e. this only bites when the flat name and
the explicit method normalize to the same owner-qualified form.

## Current behavior (verified)

`portman trace 'uop/ops.py::UPat.reduce'` → `status=not_started
confidence=ambiguous` despite an explicit `fn UPat.reduce(self: read UPat)`
existing in `uop/upat.rss` (plus the flat `upat_reduce`).

## Proposed behavior

In `auto_map` scoring (src/portman/inventory.py / matching.py), when one target
candidate is an **explicit** owner-qualified declaration (`kind=method`, dotted
qualname) and the other is an **inferred** owner form derived from a flat function
(receiver inference / `owner_prefix_aliases` / `receiver_methods`), treat the
explicit one as strictly higher precedence rather than a tie — so it wins the
upstream method and the inferred flat function is left for its own (free-function)
match or dropped, instead of poisoning both into `ambiguous`.

## Acceptance criteria

- With both `fn UPat.reduce(self: …)` and flat `upat_reduce` present, upstream
  `UPat.reduce` maps to the explicit `UPat.reduce` (confidence `auto`, not
  `ambiguous`).
- No regression in tests/name_matching.py, tests/module_matching.py, or the
  ambiguous-collision behavior for genuinely-colliding distinct symbols.

## Notes

- Low priority: one symbol. A workaround is a `[mapping.symbol_links]` forced link,
  but precedence is the cleaner general fix.
