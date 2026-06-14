# tinygrad Port Workflow TODO

These items come from using Portman to track the tinygrad RSScript/modern-c port.
They are focused on reducing false gaps, improving batch selection, and making
progress numbers more useful during an active port.

> **Status (implemented):** 9 of 11 done; 2 partial. See `CHANGELOG.md`.
> - ✅ #1 forced symbol links (`[mapping.symbol_links]` + `portman link`)
> - ✅ #2 gap reasons · ✅ #3 `portman batches` · ✅ #5 coverage by area
> - ✅ #6 `[copied]` roots · ✅ #7 `[ignore]` rules · ✅ #9 batch manifest
> - ✅ #10 `status --fail-on-regression` · ✅ #11 `gaps --explain`
> - 🟡 #4 inventory ingestion done in portman; **rsscript must emit the JSON**
> - 🟡 #8 manual `[deps].boost` shipped; a real call/import graph is still TODO

## Immediate Improvements

- [ ] **Configured symbol aliases.** Add explicit source-to-target symbol mapping
  in config.
  _Why:_ RSS currently has a single generated namespace, so valid upstream names
  such as `helpers.py::count`, `helpers.py::T`, and `tensor.py::T` may need
  compile-safe RSS names like `helpers_count` or `tensor_T_typevar`. Portman
  should treat those as intentional mappings, not false gaps.
  _Acceptance:_ config supports entries like:
  ```toml
  [mapping.symbol_aliases]
  "helpers.py::count" = "helpers.rss::helpers_count"
  "helpers.py::T" = "helpers.rss::helpers_T"
  "tensor.py::T" = "tensor.rss::tensor_T_typevar"
  ```
  `portman map`, `status`, `gaps`, and `report` all honor the alias and explain
  that it came from config.

- [ ] **Gap reason categories.** Split generic `not_started` into actionable
  reasons.
  _Why:_ one flat missing bucket hides whether work is genuinely missing, blocked
  by RSS/MC, intentionally copied, intentionally ignored, or just a name-mapping
  issue.
  _Acceptance:_ gaps can be classified as at least:
  `missing`, `alias_needed`, `type_only`, `copied_generated`, `ignored`,
  `blocked_by_rss`, `blocked_by_mc`, and `needs_review`.

- [ ] **Batch recommendation output.** Add a command that groups related gaps
  into port batches.
  _Why:_ the port is slow when driven method-by-method. Portman should suggest
  coherent groups such as "UOp constructors", "UPat fluent methods", "helper
  type aliases/constants", or "runtime/autogen copied roots".
  _Acceptance:_ a command such as `portman batches --public --limit 10` prints
  grouped symbols, suggested target files, likely blockers, and expected coverage
  impact.

## Better Progress Accounting

- [ ] **Checked RSS symbol inventory input.** Prefer a compiler-produced RSS
  inventory over scraping `.rss` source text when available.
  _Why:_ source scraping loses checked symbol identity, lowered names, visibility,
  and generated-helper distinctions.
  _Acceptance:_ Portman can ingest an inventory with `module`, `qualname`, `kind`,
  `visibility`, `source_span`, and `lowered_name`, while keeping the existing
  scraper as a fallback.

- [ ] **Coverage by source area.** Report coverage by module/folder, not only
  globally.
  _Why:_ "43.8% weighted" is less actionable than knowing tensor, dtype, helpers,
  device, uop, runtime/autogen, and examples separately.
  _Acceptance:_ `status` or `report` shows coverage buckets for configured source
  areas and highlights the lowest/highest-impact areas.

- [ ] **Generated/copied-code mode.** Track copied roots separately from
  hand-ported roots.
  _Why:_ `runtime/autogen` should mostly be copied or bound. Counting it the same
  as manually translated RSS makes progress and effort estimates misleading.
  _Acceptance:_ config can mark roots as copied/generated; reports show copied
  coverage separately and do not suggest hand-port batches for those roots unless
  requested.

- [ ] **Ignore/suppression rules with reasons.** Support durable suppressions for
  out-of-scope files or symbols.
  _Why:_ deleted or intentionally skipped areas such as broken examples should not
  keep polluting gap reports.
  _Acceptance:_ config supports reasoned ignores, for example:
  ```toml
  [ignore]
  "beautiful_mnist.py::*" = "example is not runnable / missing upstream pieces"
  "engine.py::*" = "deleted as useless port target"
  ```
  Reports include ignored counts and reasons separately from missing work.

## Planning and Regression Guards

- [ ] **Dependency-aware ordering.** Rank gaps by what they unlock.
  _Why:_ some symbols, such as `UOp.const`, `UOp.alu`, `UPat.*`, and dtype
  helpers, enable many downstream methods. Portman should push those earlier than
  isolated leaf helpers.
  _Acceptance:_ gap ranking can use call/reference/import relationships or manual
  dependency hints, and reports explain why a group is high priority.

- [ ] **Port batch manifest export.** Write selected batches to a machine-readable
  worklist.
  _Why:_ agents should be able to pick up a coherent batch without re-deriving
  the symbol set and blockers.
  _Acceptance:_ Portman can emit a manifest containing batch name, symbols,
  suggested files, blockers, expected coverage impact, and verification commands.

- [ ] **Regression guard.** Add a status comparison gate for CI or local batch
  verification.
  _Why:_ after each port batch, coverage should not accidentally drop because of
  changed mappings or deleted symbols.
  _Acceptance:_ a command such as
  `portman status --fail-on-regression previous-status.json` exits nonzero when
  implemented/verified/weighted coverage regresses without an explicit baseline
  update.

- [ ] **False-gap diagnostics.** Explain why a source and target symbol did not
  match when names look close.
  _Why:_ current false gaps from name collisions or kind mismatches require manual
  database inspection.
  _Acceptance:_ `portman gaps --explain` can say "target exists but kind differs",
  "target exists but already mapped", "alias needed", or "module collision".
