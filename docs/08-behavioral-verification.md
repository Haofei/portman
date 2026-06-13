# 08 — Behavioral Equivalence Verification Strategy

Status answers "is it built?"; **verification answers "does it behave like
upstream?"** Verification is a ladder; each rung promotes a mapping's
`verification` level, and reaching `differential`+ promotes `status` to `verified`.

## The ladder

| Level | Technique | What it proves | Cost |
|---|---|---|---|
| `signature` | static signature mapping (docs/06) | API shape matches | trivial |
| `golden` | snapshot expected outputs, replay in target | fixed cases match | low |
| `differential` | run **both** impls on shared inputs, diff outputs/errors | same behavior on those inputs | medium |
| `fuzz` | generate inputs (property/fuzz), diff both impls | edge cases, randomized | high |
| `ported_tests` | upstream's own test suite, translated, green | upstream's intent holds | high |

## Differential harness (recommended core)

A differential runner is the highest value-per-effort rung and is language-neutral:

```
for case in corpus(symbol):
    up_out  = run_upstream(symbol, case)     # e.g. python -c, importing tinygrad
    tg_out  = run_target(symbol, case)       # e.g. rss run, oracle round-trip
    assert equivalent(up_out, tg_out)        # numeric tol, error-type mapping
```

The reference target already ships such a harness:
`tinygrad-rsmc/oracle/` round-trips C-reference kernels (`oracle/c_reference/*.c`)
against `.mc`-generated output (`oracle/mc_generated/*.mc`) via `roundtrip.py`.
Wire `make verify` to it and record results back with:

```
portman set verified --upstream uop/ops.py::exec_alu --verification differential
```

## Equivalence comparators

Behavioral equality is rarely byte-equality. Define per-domain comparators:
- **numeric:** absolute/relative tolerance (floating point, reductions).
- **errors:** map upstream exception types ↔ target error variants; assert the
  *category* matches, not the message.
- **edge cases:** NaN/inf, empty shapes, overflow/truncation, dtype promotion —
  enumerate these as a required golden corpus per foundational module.

## Regression detection

Every verified case becomes a regression test. The `snapshots` history + the
port-% floor gate (docs/10) catch silent regressions: a symbol that drops from
`verified` to `implemented` (because upstream changed and re-verification is
pending) is flagged by the upgrade report's `needs_reverify`.

## Proving "target matches upstream version X"

Because inventories are `version`-keyed and verification is per-mapping, the claim
"target matches upstream `X`" reduces to: *every public upstream symbol at version
`X` has a mapping with `status ∈ {verified, diverged, deprecated}`.* That is a
single query over `symbols(version=X) ⋈ mappings`, reproducible from the committed
`curated.jsonl`.
