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
