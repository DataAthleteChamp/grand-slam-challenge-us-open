PY ?= python3

.PHONY: help test lint fmt validate model-report report install check

help:
	@echo "Targets:"
	@echo "  make install   - install the package in editable mode with dev extras"
	@echo "  make test      - run the test suite"
	@echo "  make lint      - ruff check all maintained Python"
	@echo "  make fmt       - ruff format all maintained Python"
	@echo "  make validate  - verify the canonical final archive and report"
	@echo "  make model-report - informational legacy-model/base-rate comparison"
	@echo "  make report    - regenerate RESULTS.md from the final archive"
	@echo "  make check     - lint, test, and validate"

install:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest bot/tests -q

lint:
	$(PY) -m ruff check bot scripts

fmt:
	$(PY) -m ruff format bot scripts

validate:
	$(PY) scripts/validate_archive.py

model-report:
	$(PY) scripts/validate_models.py

report:
	$(PY) scripts/summarise_final.py

check: lint test validate
