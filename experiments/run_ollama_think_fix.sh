#!/usr/bin/env bash
# Third correction: redo Ollama qwen3.5:4b thinking=ON at ctx 12288, MAX_TOKENS 6144 so it
# matches its non-thinking run's context (the main sweep ran it cramped at 8192/8192, which
# truncated the code after the <think> trace). vLLM must already be stopped (Ollama needs the
# GPU). Run AFTER run_thinking_corrections.sh. Usage: bash experiments/run_ollama_think_fix.sh
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
ENV="templates/04-vllm-mcp-coder/.env"; CONT="dockeduck-mcp-coder-bench"
EXP="dockeduck-experiments"; R="experiments/results"
log() { echo "[$(date +%H:%M:%S)] $*"; }

docker stop "$CONT" >/dev/null 2>&1 || true
docker rm   "$CONT" >/dev/null 2>&1 || true
sleep 3
if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
  log "Ollama not reachable at :11434 — start it and re-run"; exit 1
fi
log "Ollama qwen3.5:4b thinking=ON  ctx=12288 max_tokens=6144"
docker run --rm --network host --env-file "$ENV" \
  -e OLLAMA_MODEL="qwen3.5:4b" -e OLLAMA_NUM_CTX=12288 -e ENABLE_THINKING=true -e MAX_TOKENS=6144 \
  -v "$ROOT":/repo -w /repo "$EXP" python experiments/bench_real.py \
  --backend local_ollama local_ollama_rescue --tasks all \
  --output "$R/bench_ollama_qwen3_5_4b_think.csv"
log "DONE — bench_ollama_qwen3_5_4b_think.csv rewritten"
