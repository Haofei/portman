# Provenance Header Specification

Every target source file should declare where it came from. portman reads this to
establish file correspondence, audit version drift, and power bidirectional
navigation. Two formats are accepted.

## Canonical (recommended)

A comment block in the first ~40 lines. Comment leader is whatever the target
language uses (`//`, `#`, `--`, …); portman only matches the `@port` tokens.

```
// @port upstream: tinygrad/uop/ops.py
// @port version: fa400f9790ab9a684387b02e958658217b33e7c1
// @port symbols: UOp, UOpMetaClass, exec_alu
// @port status: implemented
// @port deviation: D-0001
```

| Field | Meaning |
|---|---|
| `upstream` | Upstream repo-relative path (a leading package segment like `tinygrad/` is tolerated). |
| `version`  | Upstream commit/release this file was ported from. Drift vs the pinned baseline is reported. |
| `symbols`  | Optional: the upstream symbols this file claims to cover. |
| `status`   | Optional hint; the DB status still governs. |
| `deviation`| Optional `D-NNNN` deviation id. |

## Legacy (auto-detected, flagged for upgrade)

Any free-form header mentioning an upstream path works as a fallback:

```
// 1:1 port of tinygrad/uop/ops.py — the UOp IR node ...
// Source-shaped slice of tinygrad/engine/jit.py.
```

portman extracts the `*.py` path only. `portman provenance lint` lists every file
still on the legacy form (and every file with *no* header) so they can be upgraded
incrementally. On the reference repo today: 0 canonical, ~68 path-bearing legacy
headers, 118 files relying on path-mirroring alone.

## Fallback: path mirroring

When no header is present, portman pairs files by **path stem** — `uop/ops.rss`
↔ `uop/ops.py`. This is the convention the reference port already uses, so
mapping works even before headers are added. Headers, when present, override the
stem inference (and let one target file map to a differently-named upstream file).
