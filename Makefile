.DEFAULT_GOAL := help

.PHONY: help test lint format format-check fix

help: ## List the available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: ## Run the test suite
	python3 -m pytest tests/

format: ## Format source files in-place with ruff
	ruff format .

format-check: ## Verify formatting without modifying files (used in CI)
	ruff format --check .

lint: ## Check for lint violations
	ruff check .

fix: ## Auto-fix lint violations
	ruff check --fix .

check: format-check lint test ## Run all checks (CI equivalent)
