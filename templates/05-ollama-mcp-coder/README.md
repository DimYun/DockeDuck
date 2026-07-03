# 05 · Ollama MCP Coder — a local code-writing worker (Ollama backend)

Same idea as [`04-vllm-mcp-coder`](../04-vllm-mcp-coder) — a local model that generates, runs,
tests, and self-corrects code offline and exposes it over [MCP](https://modelcontextprotocol.io) —
but backed by [**Ollama**](https://ollama.com) instead of vLLM. Two containers wired together with
`docker compose`: one for Ollama, one for the MCP server.

**Why pick this over template 04?**

- **Lighter & simpler** — no vLLM base image, GGUF quantization, one `docker compose up`.
- **Faster single-user latency** — llama.cpp under the hood is snappier than vLLM for one caller.
- **Any Ollama tag** — `qwen2.5-coder:3b`, `qwen2.5-coder:7b`, `mistral:3b`, `qwen3:4b`, …

Template 04 (vLLM + AWQ) still edges it on quality-per-VRAM for the hardest tasks. Run the
benchmark and choose per your GPU.

**Minimum hardware:** 6 GB VRAM runs `qwen2.5-coder:3b` (~2 GB VRAM).

---

## How it works

```
You / cloud LLM → write_and_fix(spec) → MCP server ──native /api/chat──▶ Ollama (GGUF model)
                                              │
                                              ▼  offline, zero cloud tokens
                                  conditions → tests → code → fix loop
                                  (syntax → exec → pytest, up to MAX_RETRIES)
                                              │
                            success → DONE + code   ·   failure → structured block → cloud rescue
```

`docker-compose.yml` defines two services:

- **`ollama`** — `ollama/ollama:latest`, GPU-enabled, model data on a named volume.
- **`mcp-server`** — this template's FastMCP server (non-root), SSE on port **8000**,
  waits for Ollama to be healthy and **auto-pulls the configured model on first run**.

> **Endpoint note:** the MCP server talks to Ollama's **native `/api/chat`**, not the OpenAI
> `/v1` endpoint. Only the native endpoint honours `think: false` — on `/v1`, Qwen3/Qwen3.5
> keep emitting `<think>…</think>` and burn ~10× the tokens. See `src/vlm.py`.

---

## Setup

```bash
cd templates/05-ollama-mcp-coder

cp .env.example .env          # OLLAMA_MODEL defaults to qwen2.5-coder:3b
make build                    # build the MCP server image
make up                       # start Ollama + MCP (first run pulls the model — minutes)
make logs                     # watch for "[entrypoint] Starting MCP server"
```

- MCP SSE endpoint: `http://localhost:8000/sse`
- Ollama API: `http://localhost:11434`

Pull a different/updated model any time: `make pull-model` (reads `OLLAMA_MODEL` from `.env`).
Stop everything with `make down`. From the repo root you can also use
`make start-ollama` / `make wait-ollama` / `make stop-ollama`.

---

## The MCP tools

Same surface as template 04 — `write_input_file`, `write_and_fix`, `validate_output_file`, plus
the hardware advisors `recommend_model` and `recommend_context_window` — and the **same spec
format** (`conditions:` in natural language, or `tests:` as pytest code). See
[template 04's README](../04-vllm-mcp-coder/README.md#the-mcp-tools) and the ready-made specs in
[`experiments/tasks/`](../../experiments/tasks).

**Ollama bonus:** because Ollama loads models on demand and honours a per-request context, the
advisor tools accept `apply=true` to switch the **live** server to the recommended model/context
with no restart — e.g. *"call recommend_model with apply=true"*.

**Connect it (Claude Code):**
```bash
claude mcp add dockeduck-ollama-coder --transport sse http://localhost:8000/sse
```
`make claude-config` prints the Desktop JSON snippet.

---

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5-coder:3b` | Any Ollama tag; auto-pulled on first start |
| `OLLAMA_NUM_CTX` | `16384` | Context window (KV cache grows with this) |
| `MAX_RETRIES` | `7` | Fix iterations before `write_and_fix` returns a rescue block |
| `ENABLE_THINKING` | `false` | Keep Qwen3/Qwen3.5 chain-of-thought (native `think:` flag) |
| `TEMPERATURE` / `MAX_TOKENS` | `0.1` / `4096` | Generation controls |
| `MCP_PORT` / `OLLAMA_PORT` | `8000` / `11434` | Exposed ports |

### Model / context sizing (6 GB GPU)

| Model | ~VRAM | Suggested `OLLAMA_NUM_CTX` |
|---|---|---|
| `qwen2.5-coder:3b` | ~2 GB | up to ~20K |
| `mistral:3b` | ~2 GB | up to ~32K |
| `qwen2.5-coder:7b` | ~4 GB | ~8K (tight on RTX 4050) |

---

## Benchmarks

Run from the **repo root** with Ollama up:

```bash
make start-ollama && make wait-ollama
make exp-build
make exp-bench-ollama                              # all tasks, zero cloud cost
make exp-try FRAMEWORK=ollama MODEL=qwen2.5-coder:7b CTX=8192 TASK=class   # quick manual test
```

Results land in `experiments/results/*.csv`.

> **Numbers pending re-run** under the current local-writes-tests + on-failure-rescue
> architecture. Methodology and the latest figures live in
> [`experiments/experiments.md`](../../experiments/experiments.md).

---

## Files

```
05-ollama-mcp-coder/
├── docker-compose.yml   # ollama + mcp-server services
├── Dockerfile           # non-root MCP server image
├── entrypoint.sh        # wait for Ollama → pull model → start MCP
├── requirements.txt     # mcp, httpx, pytest, pyyaml
├── Makefile             # build / up / down / pull-model / logs / claude-config
├── .env.example         # every knob, documented
└── src/
    ├── server.py        # 5 MCP tools (write/validate + recommend_model/context, apply=true)
    ├── vlm.py           # CoderClient → Ollama native /api/chat + fence/think stripping
    ├── validator.py     # check_syntax / run_code / run_tests
    ├── prompts.py       # language-aware, task-type few-shot prompt builders
    └── hardware.py      # GPU/CPU detection + benchmark-informed model/context advice
```
