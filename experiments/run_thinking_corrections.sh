#!/usr/bin/env bash
# Corrective runs so thinking / non-thinking use the SAME (maximum-fitting) context per
# model, and the failed vLLM thinking run is redone with MAX_TOKENS < context.
#
# Context matrix (biggest that fits 6 GB; identical for both modes of a model):
#   vLLM   Qwen3-4B-AWQ      8192   (8 KV heads — KV-cache bound)
#   Ollama qwen3.5:4b       12288
# Output budget (MAX_TOKENS): 4096 non-thinking · 6144 thinking (< context; leaves room
# for the <think> trace + the code).
#
# Order is vLLM first, then Ollama (they cannot share the 6 GB GPU).
# Run AFTER run_thinking_ollama.sh finishes. Usage: bash experiments/run_thinking_corrections.sh
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
ENV="templates/04-vllm-mcp-coder/.env"; CONT="dockeduck-mcp-coder-bench"
EXP="dockeduck-experiments"; R="experiments/results"
log() { echo "[$(date +%H:%M:%S)] $*"; }
set_env() { local k="$1" v="$2"; grep -q "^$k=" "$ENV" && sed -i "s|^$k=.*|$k=$v|" "$ENV" || printf '%s=%s\n' "$k" "$v" >> "$ENV"; }
free_ollama() { for m in $(ollama ps 2>/dev/null | awk 'NR>1{print $1}'); do ollama stop "$m" 2>/dev/null || true; done; sleep 3; }

# ── A. vLLM Qwen3-4B thinking=ON, context 8192, MAX_TOKENS 6144 (fixes the HTTP 400) ──
log "=== A: Qwen3-4B-AWQ vLLM thinking=ON (ctx 8192, max_tokens 6144) ==="
free_ollama
set_env VLM_MODEL "Qwen/Qwen3-4B-AWQ"
set_env VLM_MAX_MODEL_LEN 8192
make start-bench >/dev/null 2>&1
t=0; ready=0
while [ "$t" -lt 600 ]; do
  docker ps --format '{{.Names}}' | grep -q "^${CONT}$" || { log "  vLLM exited"; docker logs --tail 15 "$CONT" 2>&1|sed 's/^/    /'; break; }
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/v1/models 2>/dev/null || true)" = "200" ] && { ready=1; log "  vLLM ready (${t}s)"; break; }
  sleep 5; t=$((t+5))
done
if [ "$ready" = 1 ]; then
  docker run --rm --network host --env-file "$ENV" -e ENABLE_THINKING=true -e MAX_TOKENS=4096 \
    -v "$ROOT":/repo -w /repo "$EXP" python experiments/bench_real.py \
    --backend local_vllm local_vllm_rescue --tasks all --output "$R/bench_qwen3_4b_think.csv"
else
  log "  SKIPPED vLLM thinking run (not ready)"
fi

# ── B. Ollama qwen3.5:4b non-thinking at 12288 (context parity with its thinking run) ──
log "=== B: Ollama qwen3.5:4b thinking=OFF at ctx 12288 (parity with thinking run) ==="
docker stop "$CONT" >/dev/null 2>&1 || true
docker rm   "$CONT" >/dev/null 2>&1 || true
sleep 3
if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
  docker run --rm --network host --env-file "$ENV" \
    -e OLLAMA_MODEL="qwen3.5:4b" -e OLLAMA_NUM_CTX=12288 -e ENABLE_THINKING=false -e MAX_TOKENS=4096 \
    -v "$ROOT":/repo -w /repo "$EXP" python experiments/bench_real.py \
    --backend local_ollama local_ollama_rescue --tasks all --output "$R/bench_ollama_qwen3_5_4b.csv"
else
  log "  Ollama not reachable — skipped B"
fi

log "=== corrections DONE ==="