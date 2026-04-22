# =============================================================================
# DockDuck Root Makefile — build/test all templates
# Goals: one-command project creation, CI-friendly
# Usage:
#   make list           # show templates
#   make build-all      # build every image
#   make test           # run pytest
#   make lint           # run ruff and pylint
# =============================================================================

TEMPLATES := 01-base-cuda 02-pytorch-lightning 03-fastapi-service

.PHONY: list build-all setup-dev lint help test

list:
	@echo "Available templates: $(TEMPLATES)"

build-all:
	@for t in $(TEMPLATES); do \
		echo "Building $$t..."; \
		$(MAKE) -C templates/$$t build || exit 1; \
	done

setup-dev:
	pip install ruff pytest pylint

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

lint: ## Run linters across all templates
	@echo "Running ruff..."
	ruff check templates/
	@echo "Running pylint..."
	pylint --disable=import-error templates/*/src/*.py templates/*/app/*.py || true

test: ## Run the automated tests for all templates
	pytest tests/test_templates.py -v

.PHONY: lint lint-deps

lint-deps: ## Install development dependencies (ruff, pylint)
	@echo "Installing linters..."
	pip install -q ruff pylint
