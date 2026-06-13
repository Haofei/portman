# 11 — Dashboard & Reporting Architecture

## One read model, many renderers

All reports are pure functions of `progress.py` + `diff.py` over the DB. No
renderer has its own logic, so the committed dashboard, the JSON feed, the CI
summary, and any future web UI can never disagree.

```
DB ─▶ progress.coverage / gaps / unverified / diverged ─▶ report.dashboard_md  ─▶ reports/dashboard.md
   └─▶ diff.upgrade_report ───────────────────────────▶ report.upgrade_md    ─▶ reports/upgrade_*.md
   └─▶ (same data) ──────────────────────────────────▶ coverage.json (machine) ─▶ web UI / Slack / badges
```

## Artifacts

| File | Audience | Contents |
|---|---|---|
| `reports/dashboard.md` | humans / PR summary | overall %, public-API %, verified %, status & kind breakdown, top risk-ranked gaps, verification backlog, deviations |
| `reports/coverage.json` | machines | full coverage + gaps + unverified + diverged arrays |
| `reports/upgrade_*.md` | release planning | the migration plan (docs/09) |
| `snapshots` table | trends | time series of the three headline metrics |

## Historical trends

`db.add_snapshot` is called on every `report` run; `db.history(metric)` returns
`(ts, version, value)` series. Render as a sparkline/CSV for a burn-up chart, or
expose as a badge endpoint (`weighted_pct`).

## Suggested dashboards to build on `coverage.json`

- **Burn-up** of `weighted_pct` and `public_api_pct` over time.
- **Heatmap** by `path` (file) × status — find the cold modules at a glance.
- **Ownership view** — group gaps by `owner`/`reviewer`.
- **Verification funnel** — counts at each `verification` level.

All are derivable from the existing JSON; no schema changes needed.
