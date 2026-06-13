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

## Scoring

- **Weighted %** = Σ weight / N — the single headline number (42.6% today).
- **Public-API %** = public symbols at ≥`implemented` / public total (54.1%).
  Public/internal is derived from the leading-underscore convention at extraction.
- **Verified %** = `verified` / N (0.0% until verification is wired).

Computed per `kind` too, which surfaced a real insight on the reference port:
**function/method name-coverage is high but class-level structural correspondence
is low** (24/404 classes) — because `rsscript` models Python classes as `struct`s
with method functions rather than 1:1 named types.

## Ownership & review

`owner` and `reviewer` live on each mapping. Assign in bulk by path prefix
(a CODEOWNERS-style convention) or per symbol via `portman set --owner`.

## History & trends

Every `report` run appends `weighted_pct`, `public_api_pct`, `verified_pct` to the
`snapshots` table keyed by timestamp + upstream version. `db.history(metric)`
returns the series for trend charts; CI can fail a PR that lowers the floor
(`docs/10`).
