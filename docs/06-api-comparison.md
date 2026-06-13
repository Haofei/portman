# 06 — API Comparison Engine

## Signature normalization

At extraction, each callable's signature is normalized to a stable string and
hashed (`sig_hash`). The Python adapter (`python_ast._sig`) captures, whitespace-
insensitively:

- positional-only / positional / `*args` / keyword-only / `**kwargs` arity & order
- parameter names
- type annotations (as source text)
- return annotation
- number of defaults (`[defaults=N]`)

So `f(a, b)` → `f(a, b, c)` and `f(a) -> int` → `f(a) -> str` both change the
hash and surface as `signature_changed` in any diff.

## Three comparisons

1. **Upstream-vs-upstream** (drift across releases): the diff engine's
   `signature_changed`. Feeds `needs_reverify`.
2. **Upstream-vs-target** (does our port match the contract?): compare the
   upstream `signature` against the linked target `signature`. Because languages
   differ syntactically, equivalence is judged by a per-adapter **signature
   mapping rule**, e.g. Python `Tensor.reshape(self, *shape)` ≈ rss
   `tensor_reshape(t, shape)`. Default rule: matched arity after dropping
   `self`/receiver and flattening varargs; tighten per project.
3. **Type behavior**: annotations are part of the signature string, so annotation
   changes are caught; deeper type-semantics (e.g. dtype promotion lattices) are
   verified behaviorally in docs/08, not statically.

## Output

`upgrade_report.needs_reverify` is the actionable artifact: every *ported* symbol
whose upstream signature **or** body changed, with its current status and owner —
144 symbols on the reference upgrade. That is the precise re-verification worklist
a release bump generates.

## Extending

To assert upstream↔target signature equivalence in CI, add a comparator in the
target adapter that maps the upstream normalized signature into the target's
convention and diffs arity/optionality. Store the result as `verification=signature`
on the mapping.
