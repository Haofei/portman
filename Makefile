PY ?= python3
export PYTHONPATH := src

.PHONY: all inventory map report status gaps diff provenance clean test

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

test:
	$(PY) -m pytest -q tests || $(PY) tests/smoke.py

clean:
	rm -f mappings/port.db
	rm -rf reports/*.md reports/*.json
