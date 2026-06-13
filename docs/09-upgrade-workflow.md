# 09 — Release Upgrade Workflow

When upstream ships a new version, this is the loop. Worked with real numbers
from `v0.13.0 → baseline fa400f9` on the reference repo.

## 1. Snapshot the new release

```bash
portman snapshot --version v0.14.0      # adds a git worktree, extracts, removes it
```

## 2. Produce the upgrade report

```bash
portman diff <old_sha> <new_sha>        # writes reports/upgrade_<old>_<new>.md
```

It classifies the upstream delta and crosses it with your port state into three
actionable buckets:

| Bucket | Meaning | Reference count |
|---|---|---:|
| `new_work` | upstream symbols added → new surface to port | 42 |
| `needs_reverify` | **ported** symbols whose upstream sig/body changed → re-verify | 144 |
| `candidate_deprecations` | upstream **removed** but we still implement → decide deprecate/keep | 0 |

(Plus `moved`: 1 — re-point the mapping rather than re-port.)

## 3. Triage

- **`new_work`** → file as port tasks; they appear in `gaps` once the baseline is
  bumped. Assign owners with `portman set in_progress --owner …`.
- **`needs_reverify`** → for each, re-run the differential harness (docs/08). If it
  still matches, restore `verified`; if upstream's behavior genuinely changed,
  update the target and re-verify.
- **`candidate_deprecations`** → either delete the target symbol, or, if you keep
  it intentionally, set `deprecated`/`diverged` with a deviation record.
- **`moved`** → the mapping follows `sid` automatically when path+qualname are
  stable; for true moves, `portman set` the new location.

## 4. Bump the baseline

Edit `portman.toml` `[upstream] version = "<new_sha>"`, then:

```bash
make inventory map report
```

Mappings keyed by `upstream_sid` carry over automatically for unchanged symbols;
only the deltas need attention.

## 5. Migration plan artifact

`reports/upgrade_<old>_<new>.md` *is* the release-to-release migration plan: a
reviewable, checklist-shaped document of exactly what the new upstream release
costs the port. Commit it alongside the baseline bump for an audit trail.
