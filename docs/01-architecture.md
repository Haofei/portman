# 01 — Architecture

## Layers

```
            ┌──────────────────────────────────────────────┐
 sources    │  upstream tree (any lang)   target tree (any lang) │
            └──────────────┬───────────────────┬───────────┘
                           │ adapters (per language)
                           ▼                   ▼
 extraction        Symbol[]  ──────────────  Symbol[]      (model.py)
                           │                   │
                           ▼                   ▼
 store                       SQLite: symbols / mappings / deviations / snapshots
                           │                   ▲
              auto-map ─────┘                   │ curated.jsonl (git source of truth)
              (provenance + path/name)          │
                           ▼                   │
 read model        progress / gaps / diff / upgrade  (progress.py, diff.py)
                           │
                           ▼
 outputs        dashboard.md · coverage.json · upgrade reports · CI summary
```

## Design principles

1. **Everything is a `Symbol`.** Files, classes, functions, methods, constants,
   types, and tests are one record type at different `kind`s. One table answers
   both "which files are ported" and "which methods are ported".

2. **Adapters isolate language knowledge.** The only language-aware code is in
   `adapters/`. Adding a runtime never touches the model, store, diff, or reports.

3. **Derived vs curated.** Inventory and auto-links are *derived* and rebuildable;
   human decisions are *curated* in `mappings/curated.jsonl`. Delete `port.db`,
   re-run, and you lose nothing that mattered.

4. **Two orthogonal axes.** `status` (how far along) and `verification` (how we
   know it matches) are independent — implemented-but-unproven is a first-class,
   visible state, not an optimistic "done".

5. **Identity survives motion.** `sid` excludes line numbers, so symbols keep
   their identity across refactors; line moves never look like add+remove.

6. **Stdlib only.** `tomllib`, `ast`, `sqlite3`, `re`. Trivial to vendor into CI.

## Data flow per command

| Command | Reads | Writes |
|---|---|---|
| `inventory` | source trees (via adapters) | `symbols` |
| `map` | `symbols` + provenance headers | `mappings` (confidence=auto) |
| `set` | — | `mappings` (confidence=manual) + `curated.jsonl` |
| `status`/`gaps`/`report` | `symbols`+`mappings` | reports, `snapshots` |
| `snapshot` | upstream git ref (worktree) | `symbols` for that version |
| `diff` | two `symbols` versions + `mappings` | upgrade report |
