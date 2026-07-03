#!/bin/bash
set -euo pipefail

: "${OLLAMA_MODEL:?OLLAMA_MODEL must be set in .env}"
: "${OLLAMA_HOST:?OLLAMA_HOST must be set}"
MCP_PORT="${MCP_PORT:-8000}"

echo "[entrypoint] Waiting for Ollama at ${OLLAMA_HOST}..."
until curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; do
    sleep 3
done
echo "[entrypoint] Ollama is up."

# Pull model on first run only
MODEL_BASE="${OLLAMA_MODEL%%:*}"
if ! curl -sf "${OLLAMA_HOST}/api/tags" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if any('${MODEL_BASE}' in m.get('name','') for m in d.get('models',[])) else 1)" 2>/dev/null; then
    echo "[entrypoint] Pulling ${OLLAMA_MODEL} (first run — may take several minutes)..."
    curl -sf -X POST "${OLLAMA_HOST}/api/pull" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"${OLLAMA_MODEL}\"}" \
        --no-buffer \
    | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
        s = d.get('status', '')
        if any(k in s for k in ('pulling', 'verifying', 'writing')):
            print(f'  {s}', flush=True)
        elif d.get('done'):
            print('[entrypoint] Model pull complete.')
    except Exception:
        pass
"
fi

echo "[entrypoint] Starting MCP server on port ${MCP_PORT}"
exec python3 -m src.server
