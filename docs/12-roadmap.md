# 12 — Implementation Roadmap

Six phases, each independently valuable. Phases 0–2 are **already implemented and
running** against the reference repos.

## Phase 0 — Inventory ✅ (done)
- Adapters for upstream + target; `portman inventory`.
- Outcome: 3,303 upstream / 3,859 target symbols catalogued.
- Exit: every file+symbol on both sides is in the DB.

## Phase 1 — Mapping ✅ (done)
- Path-mirroring + provenance-header + name-matching auto-linker (`portman map`).
- Curated JSONL source of truth; manual `portman set`.
- Collision-safe matching (target uniqueness; ambiguous links flagged, not over-claimed).
- Outcome: 121 file pairs, 1,444 symbols linked (206 ambiguous), 43.8% symbol / 44.6% public-API.
- Exit: every upstream symbol has a mapping row (linked or `not_started`).

## Phase 2 — Progress & reporting ✅ (done)
- Coverage, risk-ranked gaps, verification backlog, deviations; dashboard + JSON;
  history snapshots; upstream diff + upgrade reports.
- Exit: `make report` answers all eight headline questions.

## Phase 3 — Provenance hardening (next)
- Migrate the ~118 header-less / ~68 legacy-header files to the canonical
  `@port` block (docs/00). Add the CI provenance gate at `MAX_MISSING=0`.
- Add an upstream↔target **signature comparator** (docs/06) → `verification=signature`.
- Exit: 100% canonical provenance; signature parity reported.

## Phase 4 — Behavioral verification
- Wire `make verify` to the existing `oracle/` differential harness.
- Define equivalence comparators (numeric tol, error mapping) and golden corpora
  for foundational modules (`dtype`, `uop/ops`, `tensor`).
- Promote mappings to `verified`; stand up the CI verification gate.
- Exit: verified % becomes non-zero and ratchets; "matches upstream X" provable.

## Phase 5 — Dependency graph & test parity
- Record import/call edges into an `edges` table; replace the risk heuristic with
  topological + usage-frequency ranking (docs/05).
- Mirror upstream tests 1:1; reach `verification=ported_tests` per symbol.
- Exit: gaps ordered by true dependency; upstream test suite green in target.

## Phase 6 — Continuous upstream tracking
- Weekly cron snapshots latest upstream tag, auto-files the upgrade report,
  opens issues for `needs_reverify`.
- Port-% / verification floors ratchet automatically.
- Exit: the port stays provably in sync with upstream release-over-release.

---

### Effort signposts
- Phases 0–2: **complete** (this repo).
- Phase 3: small (header edits + one comparator).
- Phase 4: the real work — equivalence comparators + corpora per module.
- Phases 5–6: incremental hardening; each ships independent value.
