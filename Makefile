.DEFAULT_GOAL := help
PY := .venv/bin/python
SHELL := /bin/bash

.PHONY: help install check-env test lint format typecheck api mcp ui dev db-reset

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

install: ## Sync the pinned dependency set
	uv sync --all-groups

check-env: ## Fail fast if configuration is missing or unsafe
	@$(PY) -c "from statlab.config import load_settings; load_settings(); print('backend config OK')"
	@$(PY) -c "from statlab_tools.settings import load_tool_settings; load_tool_settings(); print('mcp config OK')"
	# chunk 26: append the LLM provider-key check here (must live inside llm/, D18)

test: ## Run the suite with coverage gating
	$(PY) -m pytest --cov --cov-report=term-missing

lint: ## Ruff check + format verification
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format: ## Apply formatting
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

typecheck: ## mypy --strict
	$(PY) -m mypy

api: check-env ## Run the backend alone (chunk 33+)
	@echo "not implemented until chunk 33"

mcp: check-env ## Run the MCP server alone (chunk 07+)
	$(PY) -m statlab_tools

ui: ## Run the Vite dev server alone (chunk 36+)
	@echo "not implemented until chunk 36"

dev: check-env ## Run all three processes; one Ctrl-C stops everything
	@echo "not implemented until chunk 36; check-env works today"

db-reset: ## Delete the database (chunk 05+)
	rm -f statlab.db statlab.db-wal statlab.db-shm
