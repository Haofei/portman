# 02 — Mapping Metadata Schema

Authoritative JSON Schemas: `schema/mapping.schema.json`,
`schema/inventory.schema.json`, `schema/deviation.schema.json`. This doc explains
the *why*.

## A mapping is a triple + provenance + process state

```
upstream symbol  ──(mapping)──▶  target symbol
        │                              │
   (sid, path, qualname, kind,    (sid, path, qualname, kind,
    signature, sig_hash,           signature, sig_hash)
    body_hash, version)
        │
        ├─ status         : not_started … verified | diverged | deprecated
        ├─ verification   : none | signature | golden | differential | fuzz | ported_tests
        ├─ owner, reviewer
        ├─ deviation_id   : → deviations[]   (required iff status == diverged)
        ├─ declared_*     : what the target file's header claims (audit drift)
        └─ confidence     : auto | manual | review
```

## Symbol-level traceability

Because both sides are inventoried at symbol granularity and linked by `sid`,
navigation is bidirectional:

- **upstream → target:** `portman trace uop/ops.py::UOp` prints the upstream
  record (path, line, signature) and the linked target file/line + status.
- **target → upstream:** the target file's provenance header (`@port upstream:`)
  plus the stored `mappings.target_sid` reverse lookup.

## Intentional deviations

A `diverged` mapping **must** reference a `deviation_id`. Each deviation records
`title`, `rationale`, `kind` (behavioral/api/omission/addition/perf/platform),
`approved_by`, and the `upstream_version` at which it was decided. This makes
"which differences are intentional and signed off" a query, not tribal knowledge.
Example: the reference repo's explicit-UOp-cache deviation is `D-0001` in
`mappings/curated.jsonl`.

## What is NOT in a mapping

Line ranges live on the `symbols` rows (they change constantly); the mapping
stays stable. Behavior evidence lives in the verification harness (docs/08); the
mapping only stores the resulting `verification` *level*.

## Auto-mapping rules ([mapping] config)

Matching is generic logic (strong owner-qualified vs weak bare-name forms,
exact-spelling score 4, target uniqueness) plus **project-specific naming
conventions in `portman.toml` `[mapping]`** — empty by default so the engine
stays library-agnostic:

```toml
[mapping.type_aliases]            # target name -> upstream type name(s)
TGBuffer = ["Buffer"]
[mapping.owner_prefix_aliases]    # upstream owner -> target prefixes that flatten it
DTypeMixin = ["mixin_dtype"]
[mapping.receiver_methods.UOpCache]   # flat fn(c: UOpCache, id: Int, …) is a UOp method
owner = "UOp"
strip_prefix = "uop_"
```

Receiver inference (a target free function whose first param is the receiver) is
applied to the **target side only**, so upstream Python annotations cannot mint
phantom owner forms. Conventions handled generically: trailing-underscore in-place
methods (`to_` → `tensor_to_inplace`), leading-underscore privates (kept distinct
from the public name), and dunders (`__hash__` matched verbatim, beating `hash`).

### Forced symbol links ([mapping.symbol_links])

When the matcher can't bridge a name (namespace flattening, typevars, renames),
declare it: `[mapping.symbol_links]` maps upstream `path::Qual` → target
`path::Qual`. These are re-derived from config each `map` (confidence `config`,
locked, not committed to curated.jsonl). For one-off decisions use
`portman link UP TARGET` (confidence `manual`, persisted). `gaps --explain`
flags `link_candidate`/`kind_mismatch` where a forced link is the likely fix.

### Compiler-produced inventory ([target] inventory)

Set `[target] inventory = "path/to/inv.json"` to ingest a compiler-emitted symbol
inventory instead of scraping target source text (the scraper stays the fallback
when the file is absent). Each record:

```json
{"module": "helpers", "qualname": "count", "kind": "function",
 "visibility": "public", "source_span": [12, 40], "lowered_name": "helpers_count"}
```

Because `qualname` is the **source-level** name, matching against upstream is
exact and the name-bridging heuristics stop being load-bearing; `lowered_name` is
kept for traceability. `module` stems should mirror upstream file stems so file
correspondence still works (no provenance headers in JSON).
