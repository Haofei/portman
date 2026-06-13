# 03 — Mapping Database Design

## Why SQLite + a curated JSONL sidecar

- **SQLite** gives fast relational queries over ~7k symbols and arbitrary history
  with zero services. It is the *queryable* store, fully **rebuildable** from the
  source trees plus the curated sidecar.
- **`mappings/curated.jsonl`** holds only what humans decide (manual links,
  ownership, statuses, deviations). It is the git-tracked **source of truth** and
  stays small, so PR merges touch a handful of lines — never thousands of
  auto-extracted rows. `db.export_curated` / `import_curated` round-trip it.

## Schema

```sql
symbols(sid, side, repo, path, qualname, kind, signature,
        lineno, end_lineno, version, sig_hash, body_hash, is_public,
        PRIMARY KEY (sid, version, side))          -- multi-version by design
mappings(upstream_sid PK, target_sid, status, verification, owner, reviewer,
         deviation_id, note, declared_upstream_path, declared_upstream_version,
         confidence, updated_at)
deviations(did PK, upstream_sid, title, rationale, kind, approved_by,
           upstream_version, created_at)
snapshots(ts, upstream_version, metric, value)      -- append-only trend history
```

Indexes: `(side, version)` and `(side, path)` on `symbols` cover every read in
`progress.py` and `diff.py`.

## Identity & multi-version

`sid = sha1(repo::path::qualname::kind)[:16]`. The same upstream symbol gets the
same `sid` in every snapshot, so:
- the diff engine can `JOIN` two versions on `sid`/key,
- a mapping (keyed by `upstream_sid`) automatically follows the symbol across
  upstream releases as long as its path+qualname are stable, and
- moves (path change) are detected separately and can be re-pointed.

## Confidence ladder

`auto` (machine link) < `manual` (a human ran `portman set`) < `review` (a second
human signed off). `auto_map` **never** overwrites `manual`/`review`, so re-running
extraction after upstream changes is always safe.

## Rebuild contract

```bash
rm mappings/port.db && make inventory map      # DB fully reconstructed
# curated.jsonl re-imported on next DB open; no human decision lost
```
