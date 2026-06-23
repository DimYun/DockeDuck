#!/bin/bash
set -euo pipefail

: "${VLM_MODEL:?VLM_MODEL must be set in .env}"
VLLM_PORT="${VLLM_PORT:-8001}"
MCP_PORT="${MCP_PORT:-8000}"
# VLLM_HOST: set to 0.0.0.0 only when port 8001 is exposed (start-bench target).
# Default 127.0.0.1 keeps vLLM internal in production.
VLLM_HOST="${VLLM_HOST:-127.0.0.1}"

echo "[entrypoint] Starting vLLM (model: $VLM_MODEL, host: $VLLM_HOST:$VLLM_PORT)"

python3 -m vllm.entrypoints.openai.api_server \
    --model "$VLM_MODEL" \
    --host "$VLLM_HOST" \
    --port "$VLLM_PORT" \
    --gpu-memory-utilization "${VLM_GPU_MEMORY_UTILIZATION:-0.85}" \
    --max-model-len "${VLM_MAX_MODEL_LEN:-8192}" \
    ${VLM_EXTRA_ARGS:-} &

VLLM_PID=$!

echo "[entrypoint] Waiting for vLLM to be ready..."
until curl -sf "http://127.0.0.1:${VLLM_PORT}/health" >/dev/null 2>&1; do
    if ! kill -0 "$VLLM_PID" 2>/dev/null; then
        echo "[entrypoint] vLLM process exited unexpectedly. Check logs."
        exit 1
    fi
    sleep 5
done
echo "[entrypoint] vLLM ready."

echo "[entrypoint] Starting MCP server (port: $MCP_PORT)"
exec python3 -m src.server
