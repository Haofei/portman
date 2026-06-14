# 04 — Progress Tracking Model

## Two orthogonal axes

**Status** (how far the port is) and **Verification** (how we *know* it matches)
are deliberately separate. A symbol can be `implemented` with `verification=none`
— and the dashboard shows exactly that as the verification backlog, so progress
is never overstated.

### Status states & weights (`model.WEIGHT`)

| Status | Weight | Meaning |
|---|---:|---|
| `not_started` | 0.00 | no target symbol |
| `in_progress` | 0.25 | being worked |
| `partial` | 0.50 | some behavior, known holes |
| `implemented` | 0.85 | target symbol exists & links; not behaviorally proven |
| `verified` | 1.00 | behavior proven equivalent (see verification axis) |
| `diverged` | 1.00 | intentional, documented difference (needs `deviation_id`) |
| `deprecated` | 1.00 | intentionally not ported |

`diverged`/`deprecated` score 1.0 because they are **decided** end-states — they
are out of the "to-do" denominator, not silently missing.

### Verification levels

`none → signature → golden → differential → fuzz → ported_tests`. Promotion to
status `verified` requires at least `differential` (configurable per project).

## Scoring — separate dimensions, never one blended number

`coverage()` deliberately reports several non-collapsed dimensions, because a
single headline % overstates parity (it lets "implemented" hide "unverified", and
lets file/test inventory pad the API number). Current reference values:

- **Symbol coverage** = real symbols at ≥`implemented` / all real symbols — **43.8%**.
- **Public-API coverage** = public **API-kind** symbols (class/function/method/
  constant/type) at ≥`implemented` / public API total — **44.6%** of 2,285.
  Files, modules, and tests are **excluded** from this denominator and reported
  on their own axes.
- **File coverage** = **100.0%** (every upstream file has a corresponding target file).
- **Verified %** = behaviorally proven / all — **0.0%** until verification is wired.
- **Weighted %** (planning only) = Σ weight / N — **37.3%**.
- **Parse errors** are excluded from every denominator and surfaced separately, so
  a file that fails to parse can never inflate the numbers.

Public/internal is derived from the leading-underscore convention at extraction.

Computed per `kind` too, which surfaces a real insight on the reference port:
**function/method name-coverage is high but class-level structural correspondence
is lower** — because `rsscript` models Python classes as `struct`s with method
functions rather than 1:1 named types. Note the auto-mapper **refuses
name-collision matches** (206 flagged ambiguous), so these numbers do not
double-count one target function against many upstream methods of the same name.

## Ownership & review

`owner` and `reviewer` live on each mapping. Assign in bulk by path prefix
(a CODEOWNERS-style convention) or per symbol via `portman set --owner`.

## History & trends

Every `report` run appends `weighted_pct`, `public_api_pct`, `verified_pct` to the
`snapshots` table keyed by timestamp + upstream version. `db.history(metric)`
returns the series for trend charts; CI can fail a PR that lowers the floor
(`docs/10`).
