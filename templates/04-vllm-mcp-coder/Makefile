IMAGE_NAME     ?= dockeduck-mcp-coder
CONTAINER_NAME ?= dockeduck-mcp-coder
MCP_PORT       ?= 8000
HF_CACHE       ?= $(HOME)/.cache/huggingface
UID            := $(shell id -u)
GID            := $(shell id -g)

.PHONY: build start stop logs shell debug clean claude-config

# =============================================================================
# Container lifecycle — production mode (MCP SSE on port 8000 only)
# For benchmark mode (vLLM port exposed) use: make start-bench  (from repo root)
# =============================================================================

build: ## Build the Docker image
	@[ -f .env ] || { echo "Run: cp .env.example .env  (VLM_MODEL is pre-set to Qwen)"; exit 1; }
	DOCKER_BUILDKIT=1 docker build \
		--build-arg UID=$(UID) --build-arg GID=$(GID) \
		-t $(IMAGE_NAME) .

start: ## Start container — MCP SSE on port 8000 only (production)
	@[ -f .env ] || { echo "Run: cp .env.example .env"; exit 1; }
	docker run -d \
		--name $(CONTAINER_NAME) \
		--gpus all \
		--env-file .env \
		-p $(MCP_PORT):8000 \
		-v $(HF_CACHE):/home/appuser/.cache/huggingface \
		$(IMAGE_NAME)
	@echo "MCP SSE endpoint: http://localhost:$(MCP_PORT)/sse"
	@echo "Watch startup: make logs"

stop: ## Stop and remove the container
	docker stop $(CONTAINER_NAME) 2>/dev/null || true
	docker rm   $(CONTAINER_NAME) 2>/dev/null || true

logs: ## Follow container logs
	docker logs -f $(CONTAINER_NAME)

shell: ## Exec bash into the running container
	docker exec -it $(CONTAINER_NAME) bash

debug: ## Open bash in a fresh container (skips vLLM startup)
	docker run --rm -it \
		--gpus all \
		--env-file .env \
		--entrypoint bash \
		$(IMAGE_NAME)

clean: ## Remove the Docker image
	docker rmi $(IMAGE_NAME) || true

claude-config: ## Print MCP config snippet to paste into claude_desktop_config.json
	@printf '{\n  "mcpServers": {\n    "dockeduck-vllm-coder": {\n      "url": "http://localhost:%s/sse"\n    }\n  }\n}\n' "$(MCP_PORT)"

ide-config: ## Print IDE (PyCharm / VS Code) connection info + config snippets
	@printf '\nDockeDuck MCP endpoint (SSE):  http://localhost:%s/sse\n\n' "$(MCP_PORT)"
	@printf 'VS Code   -> .vscode/mcp.json :\n  { "servers": { "dockeduck-coder": { "type": "sse", "url": "http://localhost:%s/sse" } } }\n\n' "$(MCP_PORT)"
	@printf 'PyCharm   -> Settings > Tools > AI Assistant > MCP > Add > SSE > URL above\n'
	@printf 'Continue  -> merge examples/ide/continue-config.yaml into ~/.continue/config.yaml\n'
	@printf 'No AI     -> python examples/ide/dockeduck_call.py <spec.yaml>   (pip install mcp)\n\n'
	@printf 'Full guide: docs/ide-setup.md\n'
