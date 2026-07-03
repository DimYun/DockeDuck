# 04 · vLLM MCP Coder — a local code-writing worker

Run **Qwen2.5-Coder** (or any HF model) in Docker with [vLLM](https://docs.vllm.ai) and
expose it as an [MCP](https://modelcontextprotocol.io) server. You describe a task in plain
language to a cloud LLM (Claude, GPT-4o, Gemini…) — or write a small YAML spec yourself — and
the **local model generates, runs, tests, and self-corrects the code entirely offline**.

The cloud LLM's job shrinks to two cheap things:

1. **Dispatch** — turn your request into a spec and call one tool.
2. **Rescue (only on failure)** — if the local model exhausts its retries, the tool returns a
   structured `LAST_CODE / GENERATED_TESTS / LAST_ERROR` block, and the cloud LLM makes a single
   fix. When the local model succeeds (the common case), the cloud LLM never sees the code and
   your bill stays near zero.

**Minimum hardware:** 6 GB VRAM runs `Qwen2.5-Coder-3B-Instruct-AWQ` (~2 GB VRAM).
Prefer smaller GPUs? See the sibling [`05-ollama-mcp-coder`](../05-ollama-mcp-coder) template
(Ollama backend, lighter, `docker compose`).

---

## How it works

```
You (natural language):  "binary search that raises ValueError if the target is missing,
                          handle empty list and duplicates."
        │
        ▼
  Cloud LLM (Claude / GPT-4o / Gemini)  —  or you, writing the YAML by hand
        │  writes a spec: description + conditions (or pytest tests)
        │  calls write_and_fix(spec)
        ▼
  MCP server (port 8000, SSE)
        │  Everything below runs OFFLINE on the local model — zero cloud tokens:
        ▼
  ┌───────────────────────────────────────────────────────────────┐
  │  Docker container (non-root)                                  │
  │   conditions → pytest tests   (once, before code)            │
  │   generate code                                               │
  │   for each attempt (up to MAX_RETRIES):                      │
  │     syntax check (ast.parse)  fail → fix → retry             │
  │     run code    (subprocess)  fail → fix → retry             │
  │     run tests   (pytest)      fail → fix → retry             │
  └───────────────────────────────────────────────────────────────┘
        │
        ├─ success → "# DONE after N attempt(s)" + the finished code
        └─ failure → structured block → cloud LLM does ONE rescue fix
```

Two processes share one container:

- **vLLM** serves the model on port **8001**, bound to `127.0.0.1` (internal only in production).
- **MCP server** (FastMCP, SSE) listens on port **8000** — the only port exposed.

`entrypoint.sh` starts vLLM, polls `/health` until ready, then launches the MCP server.
A non-root `appuser` (UID/GID from the host) owns every process and file.

---

## Setup

```bash
cd templates/04-vllm-mcp-coder

cp .env.example .env         # VLM_MODEL already defaults to Qwen2.5-Coder-3B-AWQ
make build                   # pulls the vLLM base image (~15 min first time)
make start                   # MCP SSE on :8000, vLLM stays internal on :8001
make logs                    # wait for "[entrypoint] vLLM ready."
```

Model weights (~1.9 GB) download once into `~/.cache/huggingface`. To go fully offline after
that, set `HF_HUB_OFFLINE=1` in `.env`.

Stop with `make stop`. For benchmark mode (vLLM port exposed) use `make start-bench` from the
repo root — see [Benchmarks](#benchmarks).

---

## The MCP tools

| Tool | What it does |
|---|---|
| `write_input_file` | Save a task spec (YAML) from a name + description + tests. The cloud LLM calls this first when it authored the tests. |
| `write_and_fix` | **The workhorse.** Generate code from a spec and fix it until syntax + exec + tests all pass, entirely on the local model. |
| `validate_output_file` | Run a spec's acceptance tests against code you already have (manual edits, CI, re-checks). |
| `recommend_model` | **Hardware advisor.** Detects this machine's GPU and proposes the best model + context window from the benchmark data (`prefer=quality\|context\|speed`). Returns the exact `.env` change to apply. |
| `recommend_context_window` | Computes the largest `VLM_MAX_MODEL_LEN` that fits your GPU for a given model (context is KV-head-bound, so a bigger model can fit *less*). |

> **First-run tip:** ask your cloud LLM *"call recommend_model to pick the best setup for my
> GPU"* before you edit `.env`. It reads your actual VRAM and returns validated settings.

### Spec format

A spec is YAML (recommended) or JSON. It carries either **`conditions:`** (natural language —
the local model writes the pytest tests) or **`tests:`** (pytest code used as-is):

```yaml
name: find_item
type: function              # function | class | connected functions | module | project
filename: find_item.py
language: python            # python | dockerfile | yaml | json | …
description: |
  Linear search on a flat or one-level-nested list. Return [index, value];
  for a match inside a nested list return [[i, j], value]; raise ValueError if absent.
conditions: |
  - If lst is empty -> raise ValueError
  - If find_item(['a', 10], 'a') -> return [0, 'a']
  - If find_item([['a','b'], [1,5]], 5) -> return [[1, 1], 5]
```

Ready-made specs for every task type live in [`experiments/tasks/`](../../experiments/tasks).

---

## Workflow A — with a cloud LLM (recommended)

Connect the MCP server, then just talk to the cloud LLM; it drives the tools.

**Claude Code (CLI):**
```bash
claude mcp add dockeduck-vllm-coder --transport sse http://localhost:8000/sse
# verify with /mcp inside a session
```

**Claude Desktop:** `make claude-config` prints the JSON snippet to paste into
`claude_desktop_config.json`.

**GPT-4o / ChatGPT:** Settings → Connectors → Add MCP server → `http://localhost:8000/sse`,
or pass `tools=[{"type":"mcp","server_url":"http://localhost:8000/sse"}]` in the Responses API.

**Gemini CLI:** `gemini mcp add --name dockeduck-vllm-coder --url http://localhost:8000/sse`

Then describe your task and border cases in any language. The cloud LLM writes conditions/tests,
calls `write_and_fix`, and shows you the result — rescuing only if the local model fails.

## Workflow B — bare IDE (no cloud LLM)

Write the spec YAML yourself and call the tool directly from your IDE's MCP client:

```
write_and_fix(spec="/path/to/find_item.yaml")
validate_output_file(spec="/path/to/find_item.yaml", code="def find_item(...): ...")
```

---

## Choosing a model

The server serves whatever `VLM_MODEL` you set in `.env`. AWQ 4-bit is the sweet spot on small
GPUs (~1 GB VRAM per 1B params).

| Model | VRAM | Notes |
|---|---|---|
| `Qwen/Qwen2.5-Coder-1.5B-Instruct-AWQ` | ~1.0 GB | fastest; simple functions |
| `Qwen/Qwen2.5-Coder-3B-Instruct-AWQ` | ~2.0 GB | **default** — best quality-for-size we tested |
| `Qwen/Qwen2.5-Coder-7B-Instruct-AWQ` | ~4.0 GB | stronger on modules/projects |
| `Qwen/Qwen3-4B-AWQ` | ~2.3 GB | thinking-capable (set `ENABLE_THINKING=true`) |

**Thinking mode.** Qwen3/Qwen3.5 emit `<think>…</think>` reasoning by default. It is stripped
before validation regardless, but it costs tokens and time. Generation defaults to
`ENABLE_THINKING=false`; on vLLM the switch is `chat_template_kwargs.enable_thinking` (handled in
`src/vlm.py`). Turn it on only for genuinely hard tasks.

Switch models by editing `.env` and restarting:
```bash
VLM_MODEL=Qwen/Qwen2.5-Coder-7B-Instruct-AWQ
VLM_EXTRA_ARGS=--quantization awq --max-model-len 8192
make stop && make start && make logs
```

---

## Benchmarks

The benchmark harness ([`experiments/bench_real.py`](../../experiments/bench_real.py)) compares
local-only, local+rescue, and cloud-baseline backends against the **same pre-written acceptance
tests** (no self-grading). Run it from the **repo root**:

```bash
make start-bench && make wait-ready       # exposes vLLM on :8001 for the experiment container
make exp-build
make exp-bench-local                       # local vLLM only — zero cloud cost
make exp-bench-full                        # local + rescue + claude_direct  (needs ANTHROPIC_API_KEY)
make exp-try FRAMEWORK=vllm TASK=class     # quick one-off manual test
```

Results land in `experiments/results/*.csv`.

> **Numbers pending re-run.** The architecture changed (Claude-writes-tests `claude_mcp` →
> local-writes-tests with on-failure `*_rescue`), so the headline cost/quality table is being
> regenerated. See [`experiments/experiments.md`](../../experiments/experiments.md) for the
> methodology and the latest measured figures.

---

## Files

```
04-vllm-mcp-coder/
├── Dockerfile         # non-root build on vllm/vllm-openai
├── entrypoint.sh      # start vLLM → wait healthy → start MCP
├── requirements.txt   # mcp, httpx, pytest, pyyaml
├── Makefile           # build / start / stop / logs / claude-config
├── .env.example       # every knob, documented
└── src/
    ├── server.py      # 5 MCP tools (write/validate + recommend_model/context)
    ├── vlm.py         # CoderClient → vLLM /v1 + fence/think stripping
    ├── validator.py   # check_syntax / run_code / run_tests
    ├── prompts.py     # language-aware, task-type few-shot prompt builders
    └── hardware.py    # GPU detection + benchmark-informed model/context advice
```
