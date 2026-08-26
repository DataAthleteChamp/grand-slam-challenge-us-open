PY ?= python3

.PHONY: help test lint fmt validate install

help:
	@echo "Targets:"
	@echo "  make install   - install the package in editable mode with dev extras"
	@echo "  make test      - run the test suite"
	@echo "  make lint      - ruff check"
	@echo "  make fmt       - ruff format"
	@echo "  make validate  - sanity-check the models against measured base rates"

install:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest bot/tests -q

lint:
	$(PY) -m ruff check bot

fmt:
	$(PY) -m ruff format bot

validate:
	$(PY) scripts/validate_models.py
