# MCP Coder — Local Qwen as a Code-Writing Worker

Run **Qwen2.5-Coder** in Docker and expose it as an [MCP](https://modelcontextprotocol.io) server.
You describe your task and border cases in plain language to a cloud LLM (Claude, GPT-4o, Gemini…).
The cloud LLM writes formal tests and delegates code generation to the local model,
which generates, validates, and self-corrects code entirely offline.

**The cloud LLM never writes code.** It writes tests and dispatches one tool call.
All loops, all fixes, all test runs happen on Qwen. Your cloud bill stays flat regardless of task complexity.

**Minimum hardware:** 6 GB VRAM — runs `Qwen2.5-Coder-3B-Instruct-AWQ` (~2 GB VRAM).

---

## How it works

```
You (natural language): "I need a binary search function that raises ValueError if not found."
        │
        ▼
  Cloud LLM (Claude / GPT-4o / Gemini)
        │
        │  1. Understands your task + border cases
        │  2. Writes pytest acceptance tests
        │  3. Calls write_input_file → creates the spec
        │  4. Calls write_and_fix(spec) → dispatches to local model
        │
        ▼
  MCP server  (port 8000, SSE)
        │
        │  Everything below runs OFFLINE, zero cloud tokens:
        ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │  Docker container (non-root)                                         │
  │                                                                      │
  │  for each attempt (up to MAX_RETRIES):                               │
  │    Qwen generates / fixes code                                       │
  │    → syntax check (ast.parse)    fail → fix → retry                 │
  │    → run code (subprocess)       fail → fix → retry                 │
  │    → run tests (pytest)          fail → fix → retry                 │
  │    → DONE                                                            │
  └──────────────────────────────────────────────────────────────────────┘
        │
        │  "# DONE after 2 attempt(s)  +  the finished code"
        ▼
  Cloud LLM presents result to you
```

**For bare IDE use** (no cloud LLM): write a spec YAML file manually, call `write_and_fix` directly.
See the spec format below.

---

## Setup

```bash
cd templates/04-mcp-coder

# 1. Configure
cp .env.example .env
# Edit .env: set VLM_MODEL (already defaults to Qwen AWQ)
# Set ANTHROPIC_API_KEY if you use Claude

# 2. Build (downloads vLLM base image — ~15 min first time)
make build

# 3. Start
make start

# 4. Wait for "vLLM ready." in logs (~60-90 s on first start, instant after)
make logs
```

Model weights (~1.9 GB) download once on first start into `~/.cache/huggingface`.
To run fully offline after that, add `HF_HUB_OFFLINE=1` to `.env`.

---

## Available tools

| Tool | What it does |
|---|---|
| `write_input_file` | Create a task spec file (YAML) from description + tests. Cloud LLM calls this first. |
| `write_and_fix` | Generate code from a spec and fix it until all tests pass. The main workhorse. |
| `validate_output_file` | Run acceptance tests from a spec against existing code. |

---

## Workflow A — with a cloud LLM (recommended)

You talk to the cloud LLM. It handles the tools automatically.

### Step 1 — connect the MCP server

**Claude Code (CLI):**
```bash
claude mcp add qwen-coder --transport sse http://localhost:8000/sse
```
Verify: type `/mcp` inside a Claude Code session.

**Claude Desktop:**
```bash
make claude-config   # prints the JSON snippet to paste
```
Add to `~/.config/claude/claude_desktop_config.json`
(Mac: `~/Library/Application Support/Claude/claude_desktop_config.json`).

**GPT-4o / ChatGPT:**
In the ChatGPT interface → Settings → Connectors → Add MCP server → `http://localhost:8000/sse`.
Or in the Responses API:
```python
from openai import OpenAI
client = OpenAI()
response = client.responses.create(
    model="gpt-4o",
    tools=[{"type": "mcp", "server_url": "http://localhost:8000/sse"}],
    input="Write binary_search.py: binary search on a sorted list, raise ValueError if not found."
)
```

**Gemini CLI:**
```bash
gemini mcp add --name qwen-coder --url http://localhost:8000/sse
```

**JetBrains AI plugin:**
Settings → AI Assistant → MCP Servers → add `http://localhost:8000/sse`

### Step 2 — describe your task

Just talk to the cloud LLM in any language. Be as informal as you like:

```
Write binary_search.py.
It should do binary search on a sorted list and return the index.
Raise ValueError if the target isn't found.

Edge cases to handle:
- empty list should raise ValueError
- target at the very start or end
- negative numbers
- all elements the same value
```

The cloud LLM will:
1. Write formal pytest tests from your border cases
2. Call `write_input_file` to save the spec
3. Call `write_and_fix` — Qwen generates and tests until everything passes
4. Show you the result

---

## Workflow B — bare IDE (no cloud LLM)

Write a spec YAML file manually, then call tools directly from your IDE's MCP client.

### The spec file format

Specs can be YAML (human-friendly, recommended) or JSON (good for programmatic generation).
Both are accepted by all three tools.

**YAML** (`experiments/tasks/binary_search.yaml`):
```yaml
name: binary_search
filename: binary_search.py
language: python
description: |
  Binary search on a sorted list[int].
  Return the index of the target value.
  Raise ValueError if target is not in the list.
tests: |
  import pytest

  def test_empty_list_raises():
      with pytest.raises(ValueError):
          binary_search([], 1)

  def test_found():
      assert binary_search([1, 2, 3], 2) == 1

  def test_not_found_raises():
      with pytest.raises(ValueError):
          binary_search([1, 3, 5], 4)
```

**JSON** (`experiments/tasks/binary_search.json`) — identical content, preferred in some IDEs:
```json
{
  "name": "binary_search",
  "filename": "binary_search.py",
  "language": "python",
  "description": "Binary search on a sorted list[int]. Return index. Raise ValueError if not found.",
  "tests": "import pytest\n\ndef test_empty_list_raises():\n    with pytest.raises(ValueError):\n        binary_search([], 1)\n\ndef test_found():\n    assert binary_search([1, 2, 3], 2) == 1\n"
}
```

Ready-made specs for all four benchmark tasks are in `experiments/tasks/` as both `.yaml` and `.json`.

### Call write_and_fix with your spec

Pass file path or content directly:

```
write_and_fix(spec="/path/to/binary_search.yaml")
write_and_fix(spec="/path/to/binary_search.json")
```

Or pass the YAML/JSON content as a string:
```
write_and_fix(spec="name: binary_search\nfilename: binary_search.py\n...")
```

### Validate existing code

```
validate_output_file(
    spec="/path/to/binary_search.yaml",
    code="def binary_search(sorted_list, target):\n    ..."
)
```

---

## Choosing a model

The server loads whatever model you set in `VLM_MODEL` in your `.env`.
vLLM supports any model from Hugging Face that it can serve.

### Model selection guide

| Model | VRAM | Speed | Best for |
|---|---|---|---|
| `Qwen/Qwen2.5-Coder-3B-Instruct-AWQ` | ~2 GB | ~8 tok/s | Functions, classes (our tested setup) |
| `Qwen/Qwen2.5-Coder-7B-Instruct-AWQ` | ~5 GB | ~5 tok/s | Files, small modules |
| `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ` | ~10 GB | ~3 tok/s | Modules, complex codebases |
| `deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct` | ~8 GB | ~6 tok/s | Strong all-rounder |
| `codellama/CodeLlama-7b-Instruct-hf` | ~6 GB | ~5 tok/s | Classic choice, broad support |

AWQ = 4-bit quantization. Roughly 1 GB VRAM per 1B parameters (AWQ).

### How we chose Qwen2.5-Coder-3B-AWQ for this setup

For a 6 GB GPU with ~2 GB for the OS/CUDA overhead, we have about 4 GB free for the model.
- The 3B AWQ model needs ~2 GB → safe headroom
- Qwen2.5-Coder outperforms older models (CodeLlama, StarCoder) at the same size in HumanEval benchmarks
- AWQ quantization loses <2% accuracy vs fp16 while halving VRAM

### Switching to a different model

```bash
# In templates/04-mcp-coder/.env:
VLM_MODEL=Qwen/Qwen2.5-Coder-7B-Instruct-AWQ

# For models that need extra vLLM flags:
VLM_EXTRA_ARGS=--max-model-len 8192 --gpu-memory-utilization 0.90

# Rebuild and restart
make stop
make start
make logs  # watch for "vLLM ready."
```

The model downloads automatically on first start from Hugging Face.

### Using Ollama instead of vLLM

If you prefer [Ollama](https://ollama.ai) for model management:
```bash
# On host (Ollama must be running on port 11434)
ollama pull qwen2.5-coder:3b

# In .env, point to Ollama's OpenAI-compat endpoint:
VLM_URL=http://host.docker.internal:11434/v1
VLM_MODEL=qwen2.5-coder:3b
```
Ollama exposes the same OpenAI-compat API that the MCP server expects.

---

## Benchmark results

Measured on **Qwen2.5-Coder-3B-Instruct-AWQ** vs **Claude Sonnet 4.6**,
RTX-class GPU, `VLM_MAX_MODEL_LEN=8192`, `MAX_TOKENS=4096`.
All backends evaluated against the **same pre-written acceptance tests** (fair, unbiased comparison).

### Full results

| Task | Backend | Quality | Fixes | Time | Cloud tokens in+out | Cost |
|---|---|---|---|---|---|---|
| function | local\_vlm | ✓✓✓ | 1 | 35 s | 0 | **$0** |
| function | claude\_direct | ✓✓✓ | 0 | 3 s | 67+115 = 182 | $0.002 |
| function | claude\_mcp | ✓✓✓ | 1 | 48 s | 4,665+749 = 5,414 | $0.025 |
| class | local\_vlm | ✓✓✓ | 0 | 16 s | 0 | **$0** |
| class | claude\_direct | ✓✓✓ | 0 | 3 s | 121+189 = 310 | $0.003 |
| class | claude\_mcp | ✓✓✓ | 0 | 37 s | 7,380+1,349 = 8,729 | $0.042 |
| file | local\_vlm | ✓✓✗ | 4 | 140 s | 0 | **$0** |
| file | claude\_direct | ✓✓✓ | 0 | 9 s | 162+406 = 568 | $0.007 |
| file | claude\_mcp | ✓✓✗ | 4 | 509 s | 4,165+1,723 = 5,888 | $0.038 |
| module | local\_vlm | ✓✓✓ | 0 | 40 s | 0 | **$0** |
| module | claude\_direct | ✓✓✓ | 0 | 13 s | 192+1,074 = 1,266 | $0.017 |
| module | claude\_mcp | ✓✓✗ | 4 | 291 s | 4,403+1,723 = 6,126 | $0.039 |

✓✓✓ = syntax + execution + tests all pass · ✓✓✗ = runs correctly, tests fail

### What the numbers reveal

**`claude_direct` is the cheapest cloud option here** — and that's an honest result.
With pre-written expert tests handed directly to Claude, it passes them in one shot
(0 fix iterations across all tasks). Total tokens are tiny: just description → code.

**`claude_mcp` is more expensive than `claude_direct`** for these tasks — also honest.
The `write_input_file` step (Claude converting English border cases to pytest) costs
4,000–7,000 input tokens per task. That's the "understanding" overhead. When Claude
can then pass expert tests in a single generation, there's no fix loop to save on.

**`local_vlm` costs nothing** — and matches or beats cloud quality on 3 of 4 tasks.
It fails `file` (task_queue priority edge cases exceed the 3B model's reasoning at depth).

### When each approach wins

| Situation | Best choice |
|---|---|
| You have pre-written tests, simple-to-medium task | `claude_direct` — cheapest, fastest |
| You want zero API cost, function/class/module level | `local_vlm` — free, reliable |
| Task is so complex that Claude needs many fix rounds | `claude_mcp` — fix loops are free |
| You're on an air-gapped network or privacy-sensitive | `local_vlm` or `claude_mcp` |
| You want to describe border cases in plain language | `claude_mcp` — Claude writes the tests |

**The MCP advantage compounds on tasks too complex for Claude to solve in one or two tries.**
These benchmark tasks are well-specified enough that Claude solves them immediately.
In production, ambiguous specs and large codebases trigger more fix iterations —
that's where keeping the loop local pays off.

---

## Reproduce the benchmark

### Prerequisites
- Docker with NVIDIA GPU support (`nvidia-container-toolkit`)
- Anthropic API key (for `claude_direct` and `claude_mcp`)
- Container running in **bench mode** (exposes vLLM on port 8001)

### Step 1 — API key

In `templates/04-mcp-coder/.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```

### Step 2 — Start in bench mode

From the repo root:
```bash
make start-bench    # exposes vLLM on port 8001 (required for experiments)
make wait-ready     # polls until both MCP and vLLM are healthy
make health-check   # confirm: MCP OK · vLLM OK · model name shown
```

### Step 3 — Build the experiment image

```bash
make exp-build      # lightweight Python image: anthropic + pytest + httpx + pyyaml
```

### Step 4 — Run

```bash
make exp-bench-full      # all 3 backends × 4 tasks (~35-40 min)

# Or individual backends:
make exp-bench-local     # local Qwen only, no API key needed (~15 min)
make exp-bench-cloud     # Claude direct only (~8 min, ~$0.61)
make exp-bench-mcp       # Claude + MCP (~25 min, ~$0.05)
```

Results → `experiments/results/`.

### Step 5 — Benchmark your own task

```yaml
# experiments/tasks/my_task.yaml
name: rate_limiter
filename: rate_limiter.py
language: python
description: |
  Thread-safe rate limiter. RateLimiter(max_calls, period_seconds).
  allow() returns True if under the limit for the current window, False otherwise.

tests: |
  import pytest

  def test_allows_under_limit():
      r = RateLimiter(max_calls=3, period_seconds=60)
      assert r.allow() is True
      assert r.allow() is True
      assert r.allow() is True

  def test_blocks_over_limit():
      r = RateLimiter(max_calls=2, period_seconds=60)
      r.allow(); r.allow()
      assert r.allow() is False
```

Preview Claude's test generation without running the full benchmark:
```bash
make exp-custom TASK=experiments/tasks/my_task.yaml PREP_ONLY=1
```

Run all three backends on your task:
```bash
make exp-custom TASK=experiments/tasks/my_task.yaml
```

---

## Stopping

```bash
make stop-bench        # from repo root (bench mode)
# or
make -C templates/04-mcp-coder stop   # production mode
```
