# =============================================================================
# DockeDuck Root Makefile — single interface for all templates + MCP experiments
#
# All experiment targets run inside Docker (dockeduck-experiments image).
# No pip installs on the host are needed for experiments.
#
# Quick start:
#   make exp-build                 # build experiment runner image (once)
#   make start-bench               # start MCP+vLLM container (GPU needed)
#   make wait-ready                # wait until vLLM is ready (~3-10 min first run)
#   make exp-bench-local           # benchmark local VLM only
#
#   (set ANTHROPIC_API_KEY in templates/04-vllm-mcp-coder/.env for cloud phases)
#   make exp-bench-full            # all 3 backends — full comparison
#   make exp-custom-all TASK_SPEC=experiments/tasks/class-example.yaml
# =============================================================================

TEMPLATES := 01-base-cuda 02-pytorch-lightning 03-fastapi-service 04-vllm-mcp-coder 05-ollama-mcp-coder

# ── MCP container settings (04-vllm-mcp-coder / vLLM) ─────────────────────────────
MCP_IMAGE       ?= dockeduck-mcp-coder
BENCH_CONTAINER ?= dockeduck-mcp-coder-bench
MCP_PORT        ?= 8000
# vLLM is exposed on VLLM_PORT_EXT only in start-bench (benchmark mode).
# Production start keeps vLLM internal (127.0.0.1 only inside the container).
VLLM_PORT_EXT   ?= 8001
HF_CACHE        ?= $(HOME)/.cache/huggingface
MCP_ENV         := templates/04-vllm-mcp-coder/.env

# ── Ollama container settings (05-ollama-mcp-coder) ──────────────────────────
OLLAMA_DIR      := templates/05-ollama-mcp-coder
OLLAMA_ENV      := templates/05-ollama-mcp-coder/.env
OLLAMA_PORT_EXT ?= 11434

# ── Experiment runner ─────────────────────────────────────────────────────────
EXP_IMAGE ?= dockeduck-experiments
# --env-file loads all config (VLM_MODEL, ANTHROPIC_API_KEY, etc.) from .env.
# --network host lets the experiment container reach vLLM on localhost:8001 (Linux).
# macOS Docker Desktop: set VLM_URL=http://host.docker.internal:8001/v1 in .env.
_EXP_RUN = docker run --rm \
    --network host \
    --env-file $(MCP_ENV) \
    -v $(CURDIR):/repo \
    -w /repo \
    $(EXP_IMAGE)

.PHONY: list build-all help lint test setup-dev \
        exp-build \
        start-bench stop-bench logs-bench wait-ready health-check \
        start-ollama stop-ollama wait-ollama health-ollama \
        exp-bench-local exp-bench-cloud exp-bench-rescue exp-bench-full \
        exp-bench-ollama exp-bench-ollama-rescue exp-bench-project \
        exp-custom exp-custom-all exp-try \
        how-to-test

# =============================================================================
# Repo-level targets
# =============================================================================

list: ## Show available templates
	@echo "Available templates: $(TEMPLATES)"

build-all: ## Build every template Docker image
	@for t in $(TEMPLATES); do \
		echo "=== Building $$t ==="; \
		$(MAKE) -C templates/$$t build || exit 1; \
	done

help: ## List all targets with descriptions
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

# Dev tools stay on host (ruff/pylint are not runtime dependencies).
setup-dev: ## Install host dev tools: ruff + pylint (linting only — not needed for experiments)
	pip install ruff pylint

lint: ## Lint: ruff + pylint (run setup-dev first)
	ruff check templates/ experiments/
	pylint --disable=import-error templates/*/src/*.py || true

test: ## Run template tests (run setup-dev first)
	pytest tests/test_templates.py -v

# =============================================================================
# Experiment runner image
# =============================================================================

exp-build: ## Build the experiment runner image (skips if already built)
	@docker image inspect $(EXP_IMAGE) >/dev/null 2>&1 \
		&& echo "$(EXP_IMAGE) image ready." \
		|| docker build -f experiments/Dockerfile -t $(EXP_IMAGE) .

# =============================================================================
# MCP container lifecycle — bench mode (vLLM port exposed for experiments)
# =============================================================================

start-bench: ## Start MCP+vLLM container with vLLM port exposed (GPU needed)
	@[ -f $(MCP_ENV) ] || { \
		echo "ERROR: $(MCP_ENV) not found."; \
		echo "Run: cp templates/04-vllm-mcp-coder/.env.example templates/04-vllm-mcp-coder/.env"; \
		exit 1; \
	}
	@docker stop $(BENCH_CONTAINER) 2>/dev/null || true
	@docker rm   $(BENCH_CONTAINER) 2>/dev/null || true
	docker run -d \
		--name $(BENCH_CONTAINER) \
		--gpus all \
		--network host \
		--env-file $(MCP_ENV) \
		-e VLLM_HOST=0.0.0.0 \
		-v $(HF_CACHE):/home/appuser/.cache/huggingface \
		$(MCP_IMAGE)
	@echo ""
	@echo "  MCP  SSE : http://localhost:$(MCP_PORT)/sse"
	@echo "  vLLM API : http://localhost:$(VLLM_PORT_EXT)/v1"
	@echo ""
	@echo "Wait for model to load: make wait-ready"

stop-bench: ## Stop and remove the bench container
	docker stop $(BENCH_CONTAINER) 2>/dev/null || true
	docker rm   $(BENCH_CONTAINER) 2>/dev/null || true

logs-bench: ## Follow bench container logs
	docker logs -f $(BENCH_CONTAINER)

wait-ready: ## Poll until vLLM is healthy (run once after start-bench)
	@echo "Waiting for vLLM to finish loading (3-10 min on first run)..."
	@until curl -sf http://localhost:$(VLLM_PORT_EXT)/health >/dev/null 2>&1; do \
		printf "."; sleep 10; \
	done
	@echo ""
	@$(MAKE) health-check

health-check: ## Check MCP + vLLM health (exits 1 if either is not ready)
	@FAILED=0; \
	printf "  MCP  SSE  (port $(MCP_PORT))  : "; \
	mcp_code=$$(curl --max-time 3 -s -o /dev/null -w '%{http_code}' http://localhost:$(MCP_PORT)/sse 2>/dev/null); \
	if [ "$$mcp_code" = "200" ]; then \
		echo "OK"; \
	else \
		echo "NOT READY (HTTP $$mcp_code)"; FAILED=1; \
	fi; \
	printf "  vLLM API  (port $(VLLM_PORT_EXT)) : "; \
	if curl -sf --max-time 5 http://localhost:$(VLLM_PORT_EXT)/health >/dev/null 2>&1; then \
		echo "OK"; \
		printf "  vLLM model            : "; \
		curl -sf http://localhost:$(VLLM_PORT_EXT)/v1/models 2>/dev/null \
			| python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'])" \
			2>/dev/null || echo "(unknown)"; \
	else \
		echo "NOT READY"; FAILED=1; \
	fi; \
	if [ "$$FAILED" = "1" ]; then \
		echo ""; \
		echo "  Run: make start-bench && make wait-ready"; \
		exit 1; \
	fi

# =============================================================================
# Ollama container lifecycle (05-ollama-mcp-coder)
# =============================================================================

start-ollama: ## Start Ollama + MCP server via docker compose (GPU needed)
	@[ -f $(OLLAMA_ENV) ] || { \
		echo "ERROR: $(OLLAMA_ENV) not found."; \
		echo "Run: cp $(OLLAMA_DIR)/.env.example $(OLLAMA_ENV)"; \
		exit 1; \
	}
	$(MAKE) -C $(OLLAMA_DIR) up
	@echo ""
	@echo "  Ollama API : http://localhost:$(OLLAMA_PORT_EXT)"
	@echo ""
	@echo "Wait for model: make wait-ollama"

stop-ollama: ## Stop Ollama + MCP server
	$(MAKE) -C $(OLLAMA_DIR) down

wait-ollama: ## Poll until Ollama is healthy (model already pulled)
	@echo "Waiting for Ollama to become ready..."
	@until curl -sf http://localhost:$(OLLAMA_PORT_EXT)/api/tags >/dev/null 2>&1; do \
		printf "."; sleep 5; \
	done
	@echo ""
	@$(MAKE) health-ollama

health-ollama: ## Check Ollama + MCP health
	@FAILED=0; \
	printf "  Ollama API (port $(OLLAMA_PORT_EXT)) : "; \
	if curl -sf --max-time 5 http://localhost:$(OLLAMA_PORT_EXT)/api/tags >/dev/null 2>&1; then \
		echo "OK"; \
		printf "  Ollama model             : "; \
		curl -sf http://localhost:$(OLLAMA_PORT_EXT)/v1/models 2>/dev/null \
			| python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'])" \
			2>/dev/null || echo "(unknown)"; \
	else \
		echo "NOT READY"; FAILED=1; \
	fi; \
	printf "  MCP SSE   (port $(MCP_PORT))      : "; \
	mcp_code=$$(curl --max-time 3 -s -o /dev/null -w '%{http_code}' http://localhost:$(MCP_PORT)/sse 2>/dev/null); \
	if [ "$$mcp_code" = "200" ]; then echo "OK"; else echo "NOT READY (HTTP $$mcp_code)"; FAILED=1; fi; \
	if [ "$$FAILED" = "1" ]; then \
		echo ""; echo "  Run: make start-ollama && make wait-ollama"; exit 1; \
	fi

# =============================================================================
# Benchmark targets — v2 (user-YAML architecture); run inside the
# dockeduck-experiments container (no host pip installs).
#   Flow: conditions → local tests → local code → fix loop → [Claude rescue if needed]
# =============================================================================

exp-bench-local: exp-build ## v2: Local vLLM only — conditions→tests→code, zero cloud cost (GPU needed)
	@$(MAKE) health-check
	@mkdir -p experiments/results
	$(_EXP_RUN) python experiments/bench_real.py \
		--backend local_vllm --tasks all \
		--output experiments/results/bench_local.csv
	@echo "Results → experiments/results/bench_local.csv"

exp-bench-cloud: exp-build ## v2: Claude direct baseline — real cost (no GPU, ANTHROPIC_API_KEY required)
	@grep -q '^ANTHROPIC_API_KEY=.' $(MCP_ENV) 2>/dev/null || { echo "ERROR: add ANTHROPIC_API_KEY=sk-ant-... to $(MCP_ENV)"; exit 1; }
	@mkdir -p experiments/results
	$(_EXP_RUN) python experiments/bench_real.py \
		--backend claude_direct --tasks all \
		--output experiments/results/bench_cloud.csv
	@echo "Results → experiments/results/bench_cloud.csv"

exp-bench-rescue: exp-build ## v2: vLLM + Claude rescue on failure (GPU + ANTHROPIC_API_KEY)
	@$(MAKE) health-check
	@grep -q '^ANTHROPIC_API_KEY=.' $(MCP_ENV) 2>/dev/null || { echo "ERROR: add ANTHROPIC_API_KEY=sk-ant-... to $(MCP_ENV)"; exit 1; }
	@mkdir -p experiments/results
	$(_EXP_RUN) python experiments/bench_real.py \
		--backend local_vllm_rescue --tasks all \
		--output experiments/results/bench_rescue_vllm.csv
	@echo "Results → experiments/results/bench_rescue_vllm.csv"

exp-bench-full: exp-build ## v2: Full comparison: local + rescue + claude_direct (GPU + ANTHROPIC_API_KEY)
	@$(MAKE) health-check
	@grep -q '^ANTHROPIC_API_KEY=.' $(MCP_ENV) 2>/dev/null || { echo "ERROR: add ANTHROPIC_API_KEY=sk-ant-... to $(MCP_ENV)"; exit 1; }
	@mkdir -p experiments/results
	$(_EXP_RUN) python experiments/bench_real.py \
		--backend local_vllm local_vllm_rescue claude_direct --tasks all \
		--output experiments/results/bench_full.csv
	@echo "Results → experiments/results/bench_full.csv"

exp-bench-ollama: exp-build ## v2: Ollama local — all tasks, zero cloud cost (Ollama running needed)
	@$(MAKE) health-ollama
	@mkdir -p experiments/results
	$(_EXP_RUN) python experiments/bench_real.py \
		--backend local_ollama --tasks all \
		--output experiments/results/bench_ollama.csv
	@echo "Results → experiments/results/bench_ollama.csv"

exp-bench-ollama-rescue: exp-build ## v2: Ollama + Claude rescue (Ollama running + ANTHROPIC_API_KEY)
	@$(MAKE) health-ollama
	@grep -q '^ANTHROPIC_API_KEY=.' $(MCP_ENV) 2>/dev/null || { echo "ERROR: add ANTHROPIC_API_KEY=sk-ant-... to $(MCP_ENV)"; exit 1; }
	@mkdir -p experiments/results
	$(_EXP_RUN) python experiments/bench_real.py \
		--backend local_ollama local_ollama_rescue claude_direct --tasks all \
		--output experiments/results/bench_ollama_rescue.csv
	@echo "Results → experiments/results/bench_ollama_rescue.csv"

exp-bench-project: exp-build ## v2: Hardest (project) task, all backends (GPU + ANTHROPIC_API_KEY)
	@$(MAKE) health-check
	@grep -q '^ANTHROPIC_API_KEY=.' $(MCP_ENV) 2>/dev/null || { echo "ERROR: add ANTHROPIC_API_KEY=sk-ant-... to $(MCP_ENV)"; exit 1; }
	@mkdir -p experiments/results
	$(_EXP_RUN) python experiments/bench_real.py \
		--backend local_vllm local_vllm_rescue claude_direct --tasks project \
		--output experiments/results/bench_project.csv
	@echo "Results → experiments/results/bench_project.csv"

# ── Custom task benchmark ─────────────────────────────────────────────────────
# Write your YAML with conditions: then run: make exp-custom TASK_SPEC=path/to/your.yaml
TASK_SPEC ?= experiments/tasks/function-example.yaml

exp-custom: exp-build ## Custom task, local vLLM (TASK_SPEC=experiments/tasks/your_task.yaml)
	@$(MAKE) health-check
	@mkdir -p experiments/results
	$(_EXP_RUN) python experiments/custom_task.py \
		--task $(TASK_SPEC) \
		--backend local_vllm \
		--output experiments/results/custom_bench.csv
	@echo "Results → experiments/results/custom_bench.csv"

exp-custom-all: exp-build ## Custom task, all backends (GPU + ANTHROPIC_API_KEY)
	@$(MAKE) health-check
	@grep -q '^ANTHROPIC_API_KEY=.' $(MCP_ENV) 2>/dev/null || { echo "ERROR: add ANTHROPIC_API_KEY=sk-ant-... to $(MCP_ENV)"; exit 1; }
	@mkdir -p experiments/results
	$(_EXP_RUN) python experiments/custom_task.py \
		--task $(TASK_SPEC) \
		--backend all \
		--output experiments/results/custom_bench_all.csv
	@echo "Results → experiments/results/custom_bench_all.csv"

# ── Manual quick test ─────────────────────────────────────────────────────────
# One knob-driven run for a human to sanity-check a model + context window.
# Override any of these on the command line, e.g.:
#   make exp-try FRAMEWORK=ollama MODEL=qwen2.5-coder:7b CTX=8192 TASK=class
#   make exp-try FRAMEWORK=vllm   TASK=project THINKING=true
FRAMEWORK ?= vllm          # vllm | ollama
MODEL     ?=               # model id/tag; empty = use the served/.env model
CTX       ?= 16384         # context window (Ollama num_ctx)
THINKING  ?= false         # true to keep Qwen3/Qwen3.5 chain-of-thought
TASK      ?= function      # function | class | connected | module | project

exp-try: exp-build ## Manual quick test (FRAMEWORK=vllm|ollama MODEL=… CTX=… TASK=… THINKING=…)
	@mkdir -p experiments/results
	@if [ "$(FRAMEWORK)" = "ollama" ]; then \
		$(MAKE) health-ollama; \
		[ -n "$(MODEL)" ] && MODELARG="-e OLLAMA_MODEL=$(MODEL)" || MODELARG=""; \
		echo "→ Ollama  model=$${MODEL:-<.env>}  ctx=$(CTX)  thinking=$(THINKING)  task=$(TASK)"; \
		docker run --rm --network host --env-file $(MCP_ENV) \
			$$MODELARG -e OLLAMA_NUM_CTX=$(CTX) -e ENABLE_THINKING=$(THINKING) \
			-v $(CURDIR):/repo -w /repo $(EXP_IMAGE) \
			python experiments/bench_real.py --backend local_ollama --tasks $(TASK) \
			--output experiments/results/try_ollama.csv; \
		echo "Results → experiments/results/try_ollama.csv"; \
	else \
		$(MAKE) health-check; \
		[ -n "$(MODEL)" ] && MODELARG="-e VLM_MODEL=$(MODEL)" || MODELARG=""; \
		echo "→ vLLM  model=$${MODEL:-<served>}  thinking=$(THINKING)  task=$(TASK)"; \
		docker run --rm --network host --env-file $(MCP_ENV) \
			$$MODELARG -e ENABLE_THINKING=$(THINKING) \
			-v $(CURDIR):/repo -w /repo $(EXP_IMAGE) \
			python experiments/bench_real.py --backend local_vllm --tasks $(TASK) \
			--output experiments/results/try_vllm.csv; \
		echo "Results → experiments/results/try_vllm.csv"; \
	fi

# =============================================================================
# Step-by-step guide
# =============================================================================

how-to-test: ## Print step-by-step testing guide
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║       DockeDuck MCP Coder — Step-by-Step Testing Guide             ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "All commands run from repo root.  All experiments run in Docker."
	@echo ""
	@echo "── PHASE 0 — One-time setup ─────────────────────────────────────────"
	@echo ""
	@echo "  cp templates/04-vllm-mcp-coder/.env.example templates/04-vllm-mcp-coder/.env"
	@echo "  make -C templates/04-vllm-mcp-coder build   # build the MCP+vLLM image"
	@echo "  make exp-build                          # build experiment runner image"
	@echo "  # Edit $(MCP_ENV): add ANTHROPIC_API_KEY=sk-ant-...  (only for cloud phases)"
	@echo ""
	@echo "── PHASE 1 — Local vLLM benchmark  (GPU, no API key, \$$0) ───────────"
	@echo ""
	@echo "  make start-bench && make wait-ready   # start container, wait for vLLM (3-10 min)"
	@echo "  make exp-bench-local                  # → results/bench_local.csv"
	@echo ""
	@echo "  Read: quality (✓✓✓ tests / ✓✓✗ exec / ✓✗✗ syntax), fix_iters, confidence."
	@echo ""
	@echo "── PHASE 2 — Cloud baseline + rescue comparison  (API key) ─────────"
	@echo ""
	@echo "  make exp-bench-full     # local_vllm + local_vllm_rescue + claude_direct"
	@echo "  → experiments/results/bench_full.csv"
	@echo ""
	@echo "  Compare: local_vllm_rescue reaches claude_direct quality for a fraction of the"
	@echo "  cost — the local fix loop is free; Claude is billed only to rescue a failed task."
	@echo ""
	@echo "── PHASE 3 — Custom task with your own acceptance tests ────────────"
	@echo ""
	@echo "  # Create experiments/tasks/my_task.yaml with a conditions: (or tests:) block."
	@echo "  make exp-custom      TASK_SPEC=experiments/tasks/my_task.yaml   # local only"
	@echo "  make exp-custom-all  TASK_SPEC=experiments/tasks/my_task.yaml   # + rescue + cloud"
	@echo ""
	@echo "── PHASE 4 — Ollama backend (05-ollama-mcp-coder) ─────────────────"
	@echo ""
	@echo "  cp templates/05-ollama-mcp-coder/.env.example templates/05-ollama-mcp-coder/.env"
	@echo "  make start-ollama && make wait-ollama"
	@echo "  make exp-bench-ollama                 # → results/bench_ollama.csv"
	@echo "  # Default qwen2.5-coder:3b; ask the recommend_model MCP tool for your GPU."
	@echo ""
	@echo "── Full sweeps (one command each) ──────────────────────────────────"
	@echo ""
	@echo "  bash experiments/run_models.sh          # every vLLM model at its max context"
	@echo "  bash experiments/run_thinking_ollama.sh # thinking on/off + Ollama models"
	@echo ""
	@echo "  Details: experiments/RESULTS.md · experiments/experiments.md (§6)."
	@echo "  All CSVs land in experiments/results/ — open in a spreadsheet."
	@echo ""
