.DEFAULT_GOAL := help
PY := .venv/bin/python
SHELL := /bin/bash

.PHONY: help install check-env test test-fast check lint format typecheck api mcp ui dev share db-reset itest-offline itest-offline-rich itest-replay itest-record

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

install: ## Sync the pinned dependency set
	uv sync --all-groups

check-env: ## Fail fast if configuration is missing or unsafe
	@$(PY) scripts/dev.py check

test: ## Run the full suite with coverage gating
	$(PY) -c 'import subprocess,sys; sys.exit(subprocess.run(sys.argv[1:], timeout=900).returncode)' $(PY) -m pytest -q --cov --cov-report=term

test-fast: ## Run selected tests without coverage (set TESTS=...)
	@test -n "$(strip $(TESTS))" || { echo "set TESTS to one or more pytest selectors" >&2; exit 2; }
	$(PY) -c 'import subprocess,sys; sys.exit(subprocess.run(sys.argv[1:], timeout=900).returncode)' $(PY) -m pytest -q --no-cov $(TESTS)

check: test typecheck lint ## Run final full tests, typecheck, and lint once each

lint: ## Ruff check + format verification
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format: ## Apply formatting
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

typecheck: ## mypy --strict
	$(PY) -m mypy

api: check-env ## Run the backend alone (chunk 33+)
	$(PY) -m statlab.main

mcp: check-env ## Run the MCP server alone (chunk 07+)
	$(PY) -m statlab_tools

ui: ## Run the Vite dev server alone (chunk 36+)
	cd frontend && node node_modules/vite/bin/vite.js

dev: ## Run all three processes; one Ctrl-C stops everything
	$(PY) scripts/dev.py dev

share: ## Serve a built UI on 4173 for a Tailscale Funnel (Secure cookies)
	cd frontend && node node_modules/vite/bin/vite.js build
	STATLAB_COOKIE_SECURE=true $(PY) scripts/dev.py share

itest-offline: ## Integration suite under pytest, offline canned LLM
	$(PY) -m pytest -c integration/pytest.ini integration/tests --llm offline

itest-offline-rich: ## Integration suite under pytest, offline rich LLM
	$(PY) -m pytest -c integration/pytest.ini integration/tests --llm offline-rich

itest-replay: ## Integration suite replayed from recorded cassettes (no spend)
	$(PY) -m pytest -c integration/pytest.ini integration/tests --llm replay

itest-record: ## Record a live run into the cassettes (paid; needs the provider key)
	$(PY) -m pytest -c integration/pytest.ini integration/tests --llm record

db-reset: ## Delete the database (chunk 05+)
	rm -f statlab.db statlab.db-wal statlab.db-shm
