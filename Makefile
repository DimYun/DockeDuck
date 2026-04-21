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

.PHONY: list build-all setup-dev lint test

list:
	@echo "Available templates: $(TEMPLATES)"

build-all:
	@for t in $(TEMPLATES); do \
		echo "Building $$t..."; \
		$(MAKE) -C templates/$$t build || exit 1; \
	done

setup-dev:
	pip install ruff pytest pylint

lint:
	ruff check templates/
	pylint templates/*/src/*.py templates/*/app/*.py || true

test:
	pytest tests/