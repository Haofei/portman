# 05 — Upstream Diff Engine & Dependency Analysis

## Diff engine (`diff.py`)

Operates purely on stored `symbols` rows, so it is language-agnostic and works for
any two `version`-keyed snapshots.

Identity key for diffing: `(path, qualname, kind)`.

| Class | How detected |
|---|---|
| **added** | key in new, not old |
| **removed** | key in old, not new |
| **moved** | same `(qualname, kind)`, different `path`, and unique on both sides |
| **signature_changed** | key in both, `sig_hash` differs |
| **body_changed** | key in both, `sig_hash` equal, `body_hash` differs |

`sig_hash`/`body_hash` are computed once at extraction, so a diff is two indexed
reads + set math — cheap enough to run on every CI build.

Reference result (`v0.13.0 → baseline`, 160 commits): 42 added, 28 removed,
1 moved, 35 signature changes, 230 body changes.

## Snapshotting any ref safely

`portman snapshot --version <ref>` adds a detached `git worktree`, extracts that
tree into `symbols` under the resolved SHA, and removes the worktree — the live
upstream checkout is never disturbed, so multiple historical versions coexist in
one DB.

## Dependency / risk ordering

`progress._risk` ranks gaps so you port in a sensible order:

```
+3 public API   +3 foundational module (dtype, uop/ops, tensor, device, helpers)
+1 class/type   → sort desc, then by path
```

This is a pragmatic heuristic. To upgrade it to a **true dependency graph**:

1. Extend the Python adapter to record `import`/attribute edges per symbol into a
   new `edges(src_sid, dst_sid, kind)` table (the AST already walks every node).
2. Topologically sort gaps so a symbol is never recommended before its upstream
   dependencies are ported.
3. Weight by **usage frequency** = in-degree across the upstream graph (and,
   optionally, call counts from upstream's own test suite).

The risk field in `coverage.json` is the integration point; nothing downstream
changes when the graph replaces the heuristic.
