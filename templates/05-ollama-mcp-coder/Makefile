MCP_PORT    ?= 8000
OLLAMA_PORT ?= 11434
UID         := $(shell id -u)
GID         := $(shell id -g)

.PHONY: build up down pull-model logs logs-mcp shell clean claude-config

build: ## Build the MCP server image
	@[ -f .env ] || { echo "Run: cp .env.example .env"; exit 1; }
	DOCKER_BUILDKIT=1 UID=$(UID) GID=$(GID) docker compose build

up: ## Start Ollama + MCP server (GPU needed)
	@[ -f .env ] || { echo "Run: cp .env.example .env"; exit 1; }
	UID=$(UID) GID=$(GID) docker compose up -d
	@echo ""
	@echo "  MCP SSE endpoint : http://localhost:$(MCP_PORT)/sse"
	@echo "  Ollama API       : http://localhost:$(OLLAMA_PORT)"
	@echo ""
	@echo "Watch logs: make logs"
	@echo "Note: model pull happens automatically on first start (may take a few minutes)."

down: ## Stop and remove all services
	docker compose down

pull-model: ## Explicitly pull/update the configured OLLAMA_MODEL
	@[ -f .env ] || { echo "Run: cp .env.example .env"; exit 1; }
	@MODEL=$$(grep '^OLLAMA_MODEL=' .env | cut -d= -f2); \
	echo "Pulling $$MODEL..."; \
	docker compose exec ollama ollama pull "$$MODEL"

logs: ## Follow all container logs
	docker compose logs -f

logs-mcp: ## Follow MCP server logs only
	docker compose logs -f mcp-server

shell: ## Exec bash into the MCP server container
	docker compose exec mcp-server bash

clean: ## Remove images and named volumes
	docker compose down --rmi local --volumes

claude-config: ## Print the MCP config snippet to add to claude_desktop_config.json
	@printf '{\n  "mcpServers": {\n    "dockeduck-ollama-coder": {\n      "url": "http://localhost:%s/sse"\n    }\n  }\n}\n' "$(MCP_PORT)"
