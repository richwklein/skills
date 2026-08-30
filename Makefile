.DEFAULT_GOAL := help

.PHONY: help test test-py test-js lint lint-py lint-md format format-check fix fix-py fix-md

help: ## List the available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: test-py test-js ## Run all tests (Python + JavaScript)

test-py: ## Run the Python test suite
	python3 -m pytest tests/

test-js: ## Run the JavaScript test suite
	node --test tests/prepare-image/image.test.mjs

format: ## Format source files in-place with ruff
	ruff format .

format-check: ## Verify formatting without modifying files (used in CI)
	ruff format --check .

lint: lint-py lint-md ## Check for lint violations (Python + Markdown)

lint-py: ## Check Python lint violations
	ruff check .

lint-md: ## Check Markdown lint violations
	npx markdownlint-cli2

fix: fix-py fix-md ## Auto-fix lint violations (Python + Markdown)

fix-py: ## Auto-fix Python lint violations
	ruff check --fix .

fix-md: ## Auto-fix Markdown lint violations
	npx markdownlint-cli2 --fix

check: format-check lint test ## Run all checks (CI equivalent)
