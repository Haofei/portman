# 10 — CI/CD Integration

Template: `ci/github-portman.yml` (GitHub Actions; the steps are portable to any
runner). Install it as `.github/workflows/portman.yml` in the **target** repo.

## Triggers

- **pull_request / push** — audit the change.
- **schedule (weekly cron)** — check upstream for a new tag, post an upgrade report.

## Gates (fail the PR)

1. **Provenance** — `portman provenance lint`; fail if any file is missing a
   header beyond `PORTMAN_MAX_MISSING`. Drives headers to 100% over time.
2. **No port regression** — compare `portman status --json:public_api_pct` to a
   committed `port_floor.txt`; fail if it drops. Bump the floor when you make
   progress (ratchet).
3. **Deviations documented** — fail if any mapping has `status=diverged` without a
   resolvable `deviation_id` (schema-validated against `deviation.schema.json`).
4. **Behavioral verification** — `make verify` runs the differential/oracle
   harness (docs/08); gate on its exit code once wired.

## Reporting in CI

- `make report` → `reports/dashboard.md` is appended to `$GITHUB_STEP_SUMMARY`, so
  every PR shows current %, top gaps, and the verification backlog inline.
- `reports/` is uploaded as a build artifact for history.
- On the cron run, the latest upstream tag is snapshotted and diffed against the
  baseline; the upgrade report is posted/issue-filed automatically.

## Ratchet philosophy

Floors only go up. The combination of the port-% floor, the provenance gate, and
the verification gate makes it impossible to merge a change that silently reduces
coverage, drops a provenance link, or introduces an undocumented divergence.

## Performance

Whole pipeline on the reference repo (3.3k upstream + 3.9k target symbols) is a
few seconds and pure-stdlib, so it adds negligible CI time; vendor `src/portman`
directly rather than installing.
