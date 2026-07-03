#!/usr/bin/env bash
# Thinking-mode coverage + Ollama sweep for DockeDuck (companion to run_models.sh).
#
# Phase 1  cloud thinking ON      → bench_claude_direct_think.csv          (no GPU)
# Phase 2  vLLM Qwen3-4B think ON → bench_qwen3_4b_think.csv               (GPU = vLLM)
# Phase 3  Ollama sweep           → bench_ollama_*.csv                     (GPU = Ollama)
#          - qwen2.5-coder:1.5b / :3b (no thinking mode) — off only
#          - qwen3.5:4b — off AND on (thinking-capable)
#
# Only thinking-capable models are run both ways; Qwen2.5-Coder has no <think> mode, so
# a second pass would be identical. Usage: bash experiments/run_thinking_ollama.sh
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
ENV="templates/04-vllm-mcp-coder/.env"
CONT="dockeduck-mcp-coder-bench"
EXP="dockeduck-experiments"
R="experiments/results"; mkdir -p "$R"

log() { echo "[$(date +%H:%M:%S)] $*"; }
set_env() { local k="$1" v="$2"; grep -q "^$k=" "$ENV" && sed -i "s|^$k=.*|$k=$v|" "$ENV" || printf '%s=%s\n' "$k" "$v" >> "$ENV"; }

wait_vllm() {  # timeout
  local t=0 max="$1" code
  while [ "$t" -lt "$max" ]; do
    docker ps --format '{{.Names}}' | grep -q "^${CONT}$" || { log "  vLLM container exited"; docker logs --tail 15 "$CONT" 2>&1 | sed 's/^/    /'; return 1; }
    code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/v1/models 2>/dev/null || true)"
    [ "$code" = "200" ] && { log "  vLLM ready (${t}s)"; return 0; }
    sleep 5; t=$((t+5))
  done; return 1
}

# ── Phase 1: cloud thinking ON ───────────────────────────────────────────────
log "=== Phase 1: claude_direct thinking=ON ==="
docker run --rm --network host --env-file "$ENV" -e CLAUDE_THINKING=true \
  -v "$ROOT":/repo -w /repo "$EXP" python experiments/bench_real.py \
  --backend claude_direct --tasks all --output "$R/bench_claude_direct_think.csv"

# ── Phase 2: vLLM Qwen3-4B thinking ON ───────────────────────────────────────
log "=== Phase 2: Qwen3-4B-AWQ vLLM thinking=ON ==="
set_env VLM_MODEL "Qwen/Qwen3-4B-AWQ"
set_env VLM_MAX_MODEL_LEN 8192
make start-bench >/dev/null 2>&1
if wait_vllm 600; then
  docker run --rm --network host --env-file "$ENV" -e ENABLE_THINKING=true -e MAX_TOKENS=4096 \
    -v "$ROOT":/repo -w /repo "$EXP" python experiments/bench_real.py \
    --backend local_vllm local_vllm_rescue --tasks all --output "$R/bench_qwen3_4b_think.csv"
else
  log "  Qwen3-4B thinking run SKIPPED (vLLM not ready)"
fi

# ── Phase 3: Ollama sweep (free the GPU first) ───────────────────────────────
log "=== Phase 3: Ollama sweep ==="
docker stop "$CONT" >/dev/null 2>&1 || true
docker rm   "$CONT" >/dev/null 2>&1 || true
sleep 3
if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
  log "  host Ollama not reachable at :11434 — start it with 'ollama serve' and re-run Phase 3"
  exit 1
fi

run_ollama() {  # model tag num_ctx think maxtok
  local model="$1" tag="$2" ctx="$3" think="$4" maxtok="$5"
  log "  ollama $tag :: $model  ctx=$ctx thinking=$think"
  ollama pull "$model" >/dev/null 2>&1 || log "    (pull failed/skipped for $model)"
  docker run --rm --network host --env-file "$ENV" \
    -e OLLAMA_MODEL="$model" -e OLLAMA_NUM_CTX="$ctx" -e ENABLE_THINKING="$think" -e MAX_TOKENS="$maxtok" \
    -v "$ROOT":/repo -w /repo "$EXP" python experiments/bench_real.py \
    --backend local_ollama local_ollama_rescue --tasks all --output "$R/bench_${tag}.csv"
}

run_ollama "qwen2.5-coder:1.5b" "ollama_coder1_5b"        16384 false 4096
run_ollama "qwen2.5-coder:3b"   "ollama_coder3b"          16384 false 4096
run_ollama "qwen3.5:4b"         "ollama_qwen3_5_4b"        8192 false 4096
run_ollama "qwen3.5:4b"         "ollama_qwen3_5_4b_think" 12288 true  6144

log "=== ALL DONE ==="
ls -1 "$R"/bench_*think*.csv "$R"/bench_ollama_*.csv 2>/dev/null | sed 's|.*/|  |'
