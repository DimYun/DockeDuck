#!/usr/bin/env bash
# Retry the Qwen3-4B-AWQ vLLM thinking=ON run (the earlier attempt crashed on load due to
# VRAM contention right after the Ollama sweep). GPU must be free. ctx 8192, MAX_TOKENS 6144.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
ENV="templates/04-vllm-mcp-coder/.env"; CONT="dockeduck-mcp-coder-bench"
EXP="dockeduck-experiments"; R="experiments/results"
log() { echo "[$(date +%H:%M:%S)] $*"; }
set_env() { local k="$1" v="$2"; grep -q "^$k=" "$ENV" && sed -i "s|^$k=.*|$k=$v|" "$ENV" || printf '%s=%s\n' "$k" "$v" >> "$ENV"; }

for m in $(ollama ps 2>/dev/null | awk 'NR>1{print $1}'); do ollama stop "$m" 2>/dev/null || true; done
sleep 3
set_env VLM_MODEL "Qwen/Qwen3-4B-AWQ"
set_env VLM_MAX_MODEL_LEN 8192
make start-bench >/dev/null 2>&1
t=0; ready=0
while [ "$t" -lt 600 ]; do
  docker ps --format '{{.Names}}' | grep -q "^${CONT}$" || { log "vLLM exited"; docker logs --tail 20 "$CONT" 2>&1|sed 's/^/  /'; break; }
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/v1/models 2>/dev/null || true)" = "200" ] && { ready=1; log "vLLM ready (${t}s)"; break; }
  sleep 5; t=$((t+5))
done
if [ "$ready" = 1 ]; then
  docker run --rm --network host --env-file "$ENV" -e ENABLE_THINKING=true -e MAX_TOKENS=4096 \
    -v "$ROOT":/repo -w /repo "$EXP" python experiments/bench_real.py \
    --backend local_vllm local_vllm_rescue --tasks all --output "$R/bench_qwen3_4b_think.csv"
  log "DONE — bench_qwen3_4b_think.csv rewritten"
else
  log "vLLM still not ready — check GPU/logs"
fi
