PY ?= python3
export PYTHONPATH := src

.PHONY: all inventory map report status gaps diff provenance doctor verify clean test pytest

all: inventory map report   ## full refresh: extract, link, render dashboard

inventory:   ## extract upstream + target symbols into the DB
	$(PY) -m portman inventory

map:         ## auto-link via provenance headers + name matching
	$(PY) -m portman map

status:      ## print coverage summary
	$(PY) -m portman status

gaps:        ## ranked port gaps (public API first)
	$(PY) -m portman gaps --public --limit 30

report:      ## write reports/dashboard.md + coverage.json
	$(PY) -m portman report

provenance:  ## lint target provenance headers
	$(PY) -m portman provenance lint

doctor:      ## validate the setup before trusting numbers
	$(PY) -m portman doctor

# Behavioral verification hook. Runs the project's differential/oracle harness if
# one is configured via $(VERIFY_CMD); otherwise it is a no-op that says so
# (it does NOT silently pass as if verification happened).
VERIFY_CMD ?=
verify:
	@if [ -n "$(VERIFY_CMD)" ]; then \
		echo "running verifier: $(VERIFY_CMD)"; $(VERIFY_CMD); \
	else \
		echo "no VERIFY_CMD configured — wire your differential/oracle harness (docs/08). Skipping."; \
	fi

# Framework self-tests. Use pytest when available, else the dependency-free smoke
# test. Crucially: a pytest *failure* fails the build (no smoke-test fallback).
test:
	@if $(PY) -c "import pytest" 2>/dev/null; then \
		$(PY) -m pytest -q tests; \
	else \
		echo "pytest not installed — running dependency-free test runners"; \
		$(PY) tests/smoke.py && $(PY) tests/name_matching.py && $(PY) tests/classification.py && $(PY) tests/inventory_ingest.py && $(PY) tests/agnostic.py && $(PY) tests/layering.py; \
	fi

pytest:
	$(PY) -m pytest -q tests

lint:        ## ruff + import-linter (no-ops with a note if not installed)
	@command -v ruff >/dev/null && ruff check src tests || echo "ruff not installed — skipping (config in pyproject.toml)"
	@command -v lint-imports >/dev/null && lint-imports || echo "import-linter not installed — tests/layering.py enforces the same contracts"

clean:
	rm -f mappings/port.db
	rm -rf reports/*.md reports/*.json
