#!/usr/bin/env bash
# Sequential multi-model vLLM benchmark runner for DockeDuck.
#
# For each model it: rewrites VLM_MODEL + VLM_MAX_MODEL_LEN in the template .env,
# restarts the bench container, waits (bounded) for vLLM to load, then runs the local
# backends (local_vllm + local_vllm_rescue) and writes a per-model CSV. On a load crash
# (OOM) it retries once at half the context. claude_direct is model-independent and is
# run once up front (no GPU needed).
#
# Usage:  bash experiments/run_models.sh   (drive from repo root)
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENV="templates/04-vllm-mcp-coder/.env"
CONT="dockeduck-mcp-coder-bench"
EXP_IMAGE="dockeduck-experiments"
RESULTS="experiments/results"
mkdir -p "$RESULTS"

log() { echo "[$(date +%H:%M:%S)] $*"; }

set_env() {  # KEY VALUE
  local k="$1" v="$2"
  if grep -q "^$k=" "$ENV"; then
    sed -i "s|^$k=.*|$k=$v|" "$ENV"
  else
    printf '%s=%s\n' "$k" "$v" >> "$ENV"
  fi
}

wait_ready() {  # timeout_seconds
  local t=0 max="$1" code
  while [ "$t" -lt "$max" ]; do
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONT}$"; then
      log "  container exited during load — last logs:"
      docker logs --tail 20 "$CONT" 2>&1 | sed 's/^/    /'
      return 1
    fi
    code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/v1/models 2>/dev/null || true)"
    if [ "$code" = "200" ]; then log "  vLLM ready after ${t}s"; return 0; fi
    sleep 5; t=$((t + 5))
  done
  log "  TIMEOUT after ${max}s"
  return 1
}

bench_local() {  # tag
  local tag="$1"
  docker run --rm --network host --env-file "$ENV" \
    -v "$ROOT":/repo -w /repo "$EXP_IMAGE" \
    python experiments/bench_real.py \
      --backend local_vllm local_vllm_rescue --tasks all \
      --output "$RESULTS/bench_${tag}.csv"
}

run_model() {  # model max_len tag load_timeout
  local model="$1" maxlen="$2" tag="$3" tmo="${4:-600}"
  log "=== $tag :: $model  (max_len=$maxlen) ==="
  set_env VLM_MODEL "$model"
  set_env VLM_MAX_MODEL_LEN "$maxlen"
  make start-bench >/dev/null 2>&1
  if wait_ready "$tmo"; then
    bench_local "$tag"
    return 0
  fi
  # OOM/crash fallback: retry once at half the context
  local half=$((maxlen / 2))
  log "  retrying $tag at reduced context max_len=$half"
  set_env VLM_MAX_MODEL_LEN "$half"
  make start-bench >/dev/null 2>&1
  if wait_ready "$tmo"; then
    bench_local "${tag}"
    return 0
  fi
  log "  GIVING UP on $tag (does not fit this GPU)"
  return 1
}

# ── Phase 0: claude_direct baseline (model-independent, no GPU) ───────────────
log "=== claude_direct baseline (once) ==="
docker run --rm --network host --env-file "$ENV" \
  -v "$ROOT":/repo -w /repo "$EXP_IMAGE" \
  python experiments/bench_real.py \
    --backend claude_direct --tasks all \
    --output "$RESULTS/bench_claude_direct.csv"

# ── Phase 1: local models, biggest context that fits 6 GB ────────────────────
# Cached, solid AWQ code models first; then the download.
run_model "Qwen/Qwen2.5-Coder-3B-Instruct-AWQ" 24576 "coder3b"  600
run_model "Qwen/Qwen3-4B-AWQ"                    8192 "qwen3_4b" 600
run_model "Qwen/Qwen2.5-Coder-1.5B-Instruct-AWQ" 32768 "coder1_5b" 900   # downloads ~1 GB first time

log "=== ALL DONE — CSVs in $RESULTS ==="
ls -1 "$RESULTS"/bench_*.csv 2>/dev/null | sed 's/^/  /'
