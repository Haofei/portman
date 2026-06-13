# portman — 1:1 Upstream Port Management Framework

A language-agnostic framework + CLI for porting an upstream library into a target
project while maintaining a strict **1:1 mapping** between the two, proving
behavioral equivalence, and tracking upstream releases over time.

It is configured here for the reference port **`tinygrad` (Python) →
`tinygrad-rsmc` (`rsscript`/`.rss`)**, but works for any source library and any
target language via pluggable adapters.

> **It already runs against the real repos.** With zero hand-curation:
>
> | Metric | Value |
> |---|---|
> | Upstream symbols inventoried | **3,303** (118 files, excl. `runtime/autogen`) |
> | Target symbols inventoried | **3,859** |
> | File pairs established (path-mirroring + headers) | **121** |
> | Symbols auto-linked | **1,650** |
> | Weighted port completion | **42.6%** |
> | Public-API completion | **54.1%** of 2,403 public symbols |
> | Verified | **0.0%** (verification axis not yet wired) |
>
> This is intentionally *stricter* than the existing `PORT_AUDIT.md`'s "100% rough
> coverage": portman requires a symbol to be matched **inside its corresponding
> file**, not merely present somewhere in the tree.

---

## Quickstart

```bash
cd port-management
make inventory      # extract upstream + target symbols into mappings/port.db
make map            # auto-link via provenance headers + path/name correspondence
make status         # coverage summary
make report         # write reports/dashboard.md + reports/coverage.json
make provenance     # lint target provenance headers

# upstream change tracking
PYTHONPATH=src python3 -m portman snapshot --version v0.13.0
PYTHONPATH=src python3 -m portman diff <old_sha> <new_sha>     # upgrade report

# traceability + curation
PYTHONPATH=src python3 -m portman trace uop/ops.py::UOp
PYTHONPATH=src python3 -m portman set verified --upstream dtype.py::DType --kind class \
    --verification differential --owner zoe
```

Requires only Python ≥ 3.11 (stdlib `tomllib`, `ast`, `sqlite3`). No third-party deps.

---

## The questions this answers

| Question | Command |
|---|---|
| What % of upstream is ported? | `portman status` → weighted + public-API % |
| Which APIs are missing? | `portman gaps --public` (ranked by risk) |
| Which APIs are implemented but not verified? | dashboard "Verification backlog" / `coverage.json:unverified` |
| Which behaviors differ from upstream? | dashboard "Documented deviations" (status=`diverged`) |
| What changed in the latest upstream release? | `portman diff OLD NEW` → added/removed/moved/sig/body |
| What work is required to close the gap? | `gaps` + upgrade report `new_work` + `needs_reverify` |
| Can we prove the target matches upstream X? | `version`-keyed snapshots + verification status per symbol |
| Which deviations are intentional + documented? | `deviations` table / `curated.jsonl` (each has rationale + approver) |

---

## Deliverables (1–12)

### 1. Repository structure
```
port-management/
├── portman.toml                # the only thing you edit to point at repos/adapters
├── Makefile                    # make inventory|map|status|gaps|report|provenance
├── src/portman/                # the framework (stdlib-only)
│   ├── model.py                # Symbol, Mapping, Deviation, Status, Verification
│   ├── config.py               # portman.toml loader
│   ├── db.py                   # SQLite store + curated-JSONL round-trip
│   ├── inventory.py            # extraction orchestration + auto-mapping
│   ├── provenance.py           # parse target provenance headers (canonical + legacy)
│   ├── progress.py             # coverage, gaps, verification backlog, ranking
│   ├── diff.py                 # upstream version diff + upgrade report
│   ├── report.py               # markdown dashboard + JSON
│   ├── cli.py                  # the `portman` command
│   └── adapters/               # one per language; add a language = add a file
│       ├── base.py  python_ast.py  rss.py  generic.py
├── schema/                     # JSON Schemas for mapping / inventory / deviation
├── mappings/
│   ├── port.db                 # DERIVED queryable store (gitignored)
│   └── curated.jsonl           # human source of truth (git-tracked)
├── reports/                    # generated dashboards (gitignored except .gitkeep)
├── ci/github-portman.yml       # CI gate template
├── tests/smoke.py              # dependency-free end-to-end test
└── docs/                       # design docs 01–12
```

### 2. Metadata schema for source-to-target mappings
See `schema/mapping.schema.json`. Every link carries: `upstream_sid`, `target_sid`,
`status`, `verification`, `owner`, `reviewer`, `deviation_id`, declared provenance
(`declared_upstream_path/version`), and `confidence` (`auto`/`manual`/`review`).
Symbol identity (`sid`) is `sha1(repo::path::qualname::kind)[:16]` — **stable
across line moves**, so a symbol keeps its identity as the file evolves.

### 3. Mapping database design
SQLite (`db.py`), four tables: `symbols` (derived inventory, per `version`),
`mappings` (curated links), `deviations`, `snapshots` (append-only history).
**Two-tier source of truth:** the DB is fully rebuildable from source trees +
`mappings/curated.jsonl`. Only human decisions (manual links, ownership,
statuses, deviations) are committed to JSONL — so merge conflicts touch a small
file, never the 3k+ auto-extracted rows. See `docs/03-database-design.md`.

### 4. Progress tracking model
Seven states (`not_started → in_progress → partial → implemented → verified`,
plus terminal `diverged`, `deprecated`) with numeric weights for scoring
(`model.WEIGHT`). `diverged`/`deprecated` count as *done* because they are
intentional, documented end-states. **Verification is a second, orthogonal axis**
(`none/signature/golden/differential/fuzz/ported_tests`) so "implemented" never
masquerades as "proven". History is captured per run in `snapshots` for trend
lines. See `docs/04-progress-model.md`.

### 5. Automated upstream diff engine
`diff.py::upstream_diff` compares two `version`-keyed inventories and classifies
every change: **added / removed / moved** (path change, same qualname) **/
signature-changed** (via `sig_hash`) **/ body-changed** (via `body_hash`).
Snapshots of any git ref are produced without disturbing the working checkout
using `git worktree` (`portman snapshot --version <ref>`). Proven on
`v0.13.0 → baseline`: 42 added, 28 removed, 1 moved, 35 sig changes, 230 body changes.

### 6. API comparison engine
Signatures are normalized at extraction (`python_ast._sig`: positional/kw-only/
defaults arity/annotations, whitespace-insensitive) and hashed. The diff engine
flags signature drift; `upgrade_report.needs_reverify` then intersects that with
ported symbols to produce the exact re-verification worklist (144 symbols on the
reference upgrade). Cross-language signature *equivalence* rules live in the
adapter layer. See `docs/06-api-comparison.md`.

### 7. Test synchronization framework
Tests are first-class symbols (`kind=test`) on both sides, so the same coverage/
gap machinery reports **ported-test coverage** and flags upstream test changes in
the diff (`body_changed` on `kind=test`). The upgrade report's `needs_reverify`
includes changed upstream tests. See `docs/07-test-sync.md`.

### 8. Behavioral equivalence verification
A layered strategy — signature → golden/snapshot → differential → fuzz/property →
ported upstream tests — each promoting a mapping's `verification` level and, when
green, its status to `verified`. The reference target already ships an oracle
harness (`tinygrad-rsmc/oracle/`: C-reference vs `.mc`-generated round-trips) that
plugs in as the differential backend. See `docs/08-behavioral-verification.md`.

### 9. Release upgrade workflow
`snapshot` the new upstream ref → `diff` against the pinned baseline → triage the
three buckets (`new_work`, `needs_reverify`, `candidate_deprecations`) → bump the
baseline in `portman.toml` → re-`map`. Fully worked example in
`docs/09-upgrade-workflow.md`.

### 10. CI/CD integration
`ci/github-portman.yml`: gates provenance (no missing headers), port-% floor
(no regression), and deviation documentation; renders the dashboard into the job
summary; on a weekly cron, snapshots the latest upstream tag and posts an upgrade
report. See `docs/10-cicd.md`.

### 11. Dashboard & reporting architecture
All reports are pure functions of the read model (`progress.py`) → one data
source drives the committed `dashboard.md`, machine-readable `coverage.json`, the
CI summary, and historical `snapshots`. See `docs/11-dashboard.md`.

### 12. Implementation roadmap
Six phases from "inventory only" to "CI-gated behavioral equivalence". See
`docs/12-roadmap.md`.

---

## Supported capabilities (mapped to the request)

- **Incremental porting** — gaps ranked by risk/dependency; status per symbol.
- **Multi-version upstream tracking** — every inventory is `version`-keyed; any
  number of snapshots coexist in one DB.
- **Multiple target languages/runtimes** — pluggable adapters; new language = one
  adapter file or a `[adapters.x]` regex block in `portman.toml`.
- **Automated report generation** — `make report`, CI job summary.
- **Dependency-graph analysis** — risk scoring uses foundational-module heuristics
  (extendable to a true import graph; see `docs/05`).
- **Ownership & review assignment** — `owner`/`reviewer` on every mapping.
- **Regression detection** — port-% floor gate + snapshot history.
- **Historical progress tracking** — `snapshots` table + trend export.
- **Release-to-release migration planning** — upgrade report buckets.
- **Explicit deviation management** — `deviations` table, schema-validated,
  required for any `diverged` status, with rationale + approver.

---

## Extending to another target language

1. Write `src/portman/adapters/<lang>.py` subclassing `Adapter` (≈40 lines, see
   `rss.py`) **or** add a `[adapters.<lang>]` regex block to `portman.toml`.
2. Point `[target] adapter = "<lang>"`.
3. Re-run `make inventory map report`. Nothing else changes — the model, DB,
   diff, progress, and reporting are all language-agnostic.

See `docs/` for the full design of each subsystem.
