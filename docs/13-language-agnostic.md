# 13 — Language Independence (and tinygrad as an example)

portman's engine knows nothing about Python, rsscript, or tinygrad. Language
enters through exactly three seams; everything else operates on the abstract
`Symbol`/`Mapping` model.

## What is generic (no language knowledge)

`model` · `db` · `config` · `diff` · `progress` · `classify` · `report` · the CLI
· the **adapter interface**. These deal only in symbols (file/class/function/
method/constant/type/test), statuses, mappings, coverage dimensions, gap reasons,
and batches. The name-matcher reduces every identifier to a **snake_case
interlingua** so any two languages compare in one common space — that reduction
is a normalization, not a per-language assumption.

## The three language seams

1. **Adapters** (`adapters/`) — turn a source tree into `Symbol`s, and (optionally)
   parse a signature into typed params. One per language:
   - `python_ast` (upstream), `rss` (target), `generic` (regex, any language),
     `inventory_json` (ingest a compiler-emitted inventory).
   - `Adapter.arg_types(signature)` is where signature *syntax* lives. The base
     returns `[]`; only `rss` knows `read|mut|fresh`. **No signature syntax is in
     the core.**

2. **`[mapping]` conventions** (config) — how the two languages' names relate.
   All have generic defaults; the language-specific ones are **opt-in**:

   | key | default | meaning |
   |---|---|---|
   | `owner_qualified` | `true` (universal) | method `Owner.m` ↔ flattened `owner_m` |
   | `dunder_passthrough` | `false` | target keeps a verbatim leaf (Python `__hash__`) |
   | `inplace_suffix` | `""` (off) | source `Owner.m_` ↔ `owner_m<suffix>` (Python in-place) |
   | `type_aliases` / `owner_prefix_aliases` / `receiver_methods` | empty | project name maps |
   | `symbol_links` | empty | forced exact upstream→target links |

3. **Upstream file extension** — derived from the upstream adapter's globs
   (`*.py` → `py`), used to parse legacy provenance headers. Not hardcoded.

## tinygrad is the example

`portman.toml` is the example that wires the seams for Python → rsscript:

```toml
[upstream] adapter = "python"      # AST extraction
[target]   adapter = "rss"         # rsscript extraction + read|mut|fresh sig parser
[mapping]
inplace_suffix     = "_inplace"    # Tensor.to_  -> tensor_to_inplace
dunder_passthrough = true          # Tensor.__hash__ -> __hash__
# owner_qualified defaults true     # Tensor.reshape -> tensor_reshape
[mapping.receiver_methods.UOpCache] # flat fn(c: UOpCache, id: Int) is a UOp method
owner = "UOp"; strip_prefix = "uop_"
```

Remove `[mapping]` and you get a conservative, generic matcher. `tests/agnostic.py`
asserts exactly this: with default rules the Python/rss tricks are **off**
(`Tensor.to_` does not match `tensor_to_inplace`, `__hash__` is not passed
through); turning the flags on enables them; and signature parsing lives in the
adapters, not the core.

## Adding a new language pair

1. Point `[upstream]`/`[target]` at adapters — built-in (`python`, `rss`), a
   `[adapters.<lang>]` regex block, or a `JsonInventoryAdapter` via
   `[target] inventory = "..."`.
2. If your target flattens methods, give that adapter an `arg_types()` parser
   (≈12 lines, see `rss.py`).
3. Set only the `[mapping]` conventions your pair actually uses.

Nothing in `model`/`db`/`diff`/`progress`/`report` changes. The strongest path for
a precise port is the **compiler JSON inventory** (#4): source-level names match
upstream exactly, so the heuristic conventions become unnecessary.
