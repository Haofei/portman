# 07 — Test Synchronization Framework

## Tests are first-class symbols

The adapters tag test files and `test_*` functions with `kind=test` on **both**
sides. This means the entire coverage/gap/diff machinery applies to tests for
free:

- **Ported-test coverage** — `coverage.json:by_kind.test` shows how many upstream
  tests have a corresponding target test (matched by the same path-mirroring +
  name logic as code).
- **Upstream test changes** — a changed upstream test shows up as `body_changed`
  with `kind=test` in `portman diff`, and lands in the upgrade report's
  `needs_reverify` if the corresponding behavior is ported.
- **New/removed upstream tests** — `added`/`removed` with `kind=test`.

## Sync workflow on an upstream bump

```
portman snapshot --version <new>
portman diff <baseline> <new> --json | jq '.body_changed[] | select(.kind=="test")'
# → the exact upstream tests that changed; port the diff into the target test tree
```

## Three tiers of test parity

1. **Mirror** — port each upstream test file 1:1 (path-mirrored), translated to the
   target language. Tracked exactly like code.
2. **Differential** — run upstream tests' *inputs* through both implementations
   and compare outputs (docs/08). No translation needed; strongest signal.
3. **Conformance** — a shared, language-neutral test corpus (golden vectors) both
   sides must pass. The reference target's `oracle/` C-reference vectors are an
   example.

A mapping reaches `verification=ported_tests` only when the mirrored upstream
tests for that symbol pass in the target.
