# MCP Coder — Technology Deep Dive

How `templates/04-vllm-mcp-coder` was designed, built, measured, and why each decision was made.

---

## The problem

Code generation with cloud LLMs works — but the cost model is bad for iterative tasks.

When a model gets a complex coding task wrong on the first try, you send back the error,
it generates again, maybe fails again, you send the failure, and so on. Each round-trip
burns cloud tokens. A module-level task with 4 fix cycles can cost $0.47 in one session.

The root cause: the cloud model does all three roles —
generate, evaluate, and fix — and every evaluation round-trip is billed.

The insight: **what if evaluation and fixing happen locally, free of charge?**

---

## The architecture

```
User (or cloud LLM) writes a spec  →  write_and_fix(spec)  →  local model runs the loop
        conditions: or tests:              (one dispatch)               (offline)
                                                                            ↓
                                                          ┌─────────────────────────────┐
                                                          │  fix loop (offline, on Qwen) │
                                                          │  conditions → pytest tests   │
                                                          │  generate code               │
                                                          │  → syntax check              │
                                                          │  → run code                  │
                                                          │  → run tests                 │
                                                          │  → fix if needed → repeat    │
                                                          └─────────────────────────────┘
                                                                            ↓
                                        success → # DONE + code        failure → structured block
                                                                            ↓
                                                          Cloud LLM does ONE rescue fix (optional)
```

**The cloud LLM does exactly two things — both cheap:**

1. **Dispatch.** It turns the user's request into a spec (or the user writes the YAML by hand)
   and makes a single `write_and_fix` call. It does not generate the code.
2. **Rescue, only on failure.** If the local model exhausts `MAX_RETRIES`, `write_and_fix`
   returns a structured `LAST_CODE / GENERATED_TESTS / LAST_ERROR` block; the cloud LLM makes
   one targeted fix. When the local model succeeds (the common case) the cloud LLM never sees
   the code.

Everything between dispatch and result — test authoring from conditions, all code generation,
every fix iteration, all test execution — runs in the Docker MCP on the local model (e.g. Qwen),
free of cloud tokens.

---

## Step 1 — The three MCP tools (`src/server.py`)

### `write_input_file`

An optional convenience: save a spec to disk from `name`, `filename`, `description`, `tests`
(pytest), `language`. It writes a YAML spec file and returns the content, which you pass straight
to `write_and_fix`. Use it when the cloud LLM authored the tests itself; most of the time you skip
it and hand `write_and_fix` a spec that carries **`conditions:`** (natural language) instead.

Example spec (the `conditions:` form — the local model writes the tests):
```yaml
name: lru_cache
type: class
filename: lru_cache.py
language: python
description: |
  LRU Cache with get/put in O(1) using OrderedDict.
conditions: |
  - get on a missing key -> return -1
  - put beyond capacity -> evict the least-recently-used entry
```

For bare IDE use (no cloud LLM), the user writes this YAML by hand.
`experiments/tasks/class-example.yaml` is a complete example.

### `write_and_fix`

The workhorse. Takes the spec and runs the whole loop offline on the local model.

```
⓪ TESTS     conditions → pytest tests   (once, before code; skipped if spec has tests:)

① SYNTAX    ast.parse() — instant, no subprocess
            Catches: missing colons, wrong indentation, invalid syntax

② EXECUTION python3 subprocess with CODE_TIMEOUT seconds
            Catches: import errors, NameError, module-level exceptions

③ TESTS     pytest subprocess against the spec's tests

    ↑_________ error text → local model → retry from ①
               (up to MAX_RETRIES, default 7)
```

Return value on success:
```
# DONE after 2 attempt(s)
# file: lru_cache.py
# confidence: 100%

<the generated Python code>
```

On failure it returns a structured block (`# MAX_RETRIES_REACHED`, `LAST_CODE`,
`GENERATED_TESTS`, `LAST_ERROR`) so the cloud LLM can perform a single **rescue** fix.

### `validate_output_file`

Runs acceptance tests from a spec against code you supply.
Parameters: `spec` (YAML content or path), `code` (Python source string).
Useful for: verifying manually-written code, re-testing after manual edits, CI integration.

---

## Step 2 — Why the workflow is built around spec files

The spec YAML is the contract between the user and the local model.
It encodes what counts as "correct" before generation starts.

**Without a spec:** the local model generates both code and tests — a self-grading problem.
The model can write easy tests that its own code happens to pass, even if real requirements aren't met.

**With a spec:** tests are written by the cloud LLM (which understands the user's intent in any language)
or by the user directly. The local model must satisfy tests it didn't write.

This separation of concerns is the core design principle:
- **Cloud LLM**: understands intent, writes acceptance criteria
- **Local model**: generates code that satisfies the criteria

For bare IDE use: the user is the cloud LLM. They write the YAML spec (description + pytest) manually.

---

## Step 3 — Docker design

Two processes inside one container:

**vLLM** — serves Qwen on port 8001, bound to `127.0.0.1` (internal only by default).
**MCP server** — FastMCP on port 8000, SSE transport, accessible from outside.

`entrypoint.sh` starts vLLM in background, polls `/health` every 2 seconds until ready,
then starts the MCP server in foreground.

**Two start modes:**

| Mode | Command | Ports exposed | Use case |
|---|---|---|---|
| Production | `make start` | 8000 | Daily use; vLLM never reachable externally |
| Bench | `make start-bench` | 8000 + 8001 | Benchmarks; experiment container needs vLLM |

The experiment container runs `--network host` so it reaches vLLM at `localhost:8001`.
In production this port is closed — vLLM only exists inside the container's loopback.

**Non-root pattern:** `appuser` (UID/GID from the host user, passed at build time) owns
all processes. Files the container writes have the same owner as the host user.

---

## Step 4 — Model selection and loading

The server loads whatever `VLM_MODEL` is set to in `.env`. vLLM serves it with an
OpenAI-compatible API at port 8001.

**How we chose the default model for a 6 GB GPU:**

Available VRAM after CUDA runtime: ~4 GB.

1. AWQ (4-bit): ~1 GB per 1B parameters → 3B AWQ fits with headroom
2. Qwen2.5-Coder-3B-AWQ scores ~65% on HumanEval vs CodeLlama-7B at ~35%
   — newer architecture + better training data beats raw parameter count
3. 8K context window handles function → file tasks; module tasks push the limit

To switch models: edit `VLM_MODEL` (and optionally `VLM_EXTRA_ARGS`) in `.env`, then
`make stop && make start`. The model downloads automatically from Hugging Face.

**Ollama** can be used as an alternative backend — it exposes the same OpenAI-compatible API:
```bash
VLM_URL=http://host.docker.internal:11434/v1
VLM_MODEL=qwen2.5-coder:3b
```

---

## Step 5 — Fence stripping (`src/vlm.py`)

The system prompt says: *"Output only raw code with no markdown or explanation."*
Small instruction-tuned models often ignore this and wrap output in fences:

```
```python
def binary_search(...):
    ...
```
```

`ast.parse()` fails on this, triggering a wasted fix cycle. Fix: strip fences at the call layer,
before any validation runs:

```python
def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^```[a-zA-Z]*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return text.strip()
```

Applied inside `CoderClient.generate()` — every model response is cleaned before use.
The same stripping is applied to all Claude outputs in the benchmark
(Claude also adds fences on complex responses).

### Thinking-mode handling (Qwen3 / Qwen3.5)

The Qwen3 family runs an extended-thinking phase by default, wrapping its reasoning in
`<think>…</think>` before the answer. Two problems for a code worker:

1. **The trace leaks into code.** If `<think>` content survives to `ast.parse()`, every
   gate fails. So `_strip_thinking()` runs *before* fence stripping, removing the block:

   ```python
   def _strip_thinking(text: str) -> str:
       return re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL).strip()
   ```

2. **It is overhead for simple tasks — but genuinely helps hard ones.** Measured on Qwen3.5:4b
   with the 5-task benchmark: thinking OFF gives ~4–5K output tokens per iteration; thinking ON
   gives ~5.5K per iteration (reasoning is modest when the task is clear). The quality impact
   varies: `function`/`class`/`file` pass with or without thinking, but `module` (multi-function
   config loader) jumps from 67% → **100% tests pass** when thinking is ON. `complex` passes
   either way with few-shot active.

   So generation defaults to thinking **off** (`ENABLE_THINKING=false`), but the flag should be
   set per task type when reasoning matters.

   **How to suppress thinking — endpoint matters:**

   Ollama's OpenAI-compatible `/v1/chat/completions` endpoint **silently ignores** both
   `think: false` and the `/no_think` soft switch. Only the native `/api/chat` endpoint
   honours the flag. The Ollama client uses native by default:

   ```python
   # templates/05-ollama-mcp-coder: native /api/chat, NOT /v1
   resp = await client.post(f"{host}/api/chat", json={
       "model": self.model,
       "messages": messages,
       "stream": False,
       "think": self.enable_thinking,   # honoured here; ignored on /v1
       ...
   })
   text = resp.json()["message"]["content"]
   ```

   For vLLM (template 04), use `chat_template_kwargs` in the `/v1` body:

   ```python
   # templates/04-vllm-mcp-coder: vLLM /v1/chat/completions
   resp = await client.post(f"{base}/chat/completions", json={
       "model": self.model,
       "messages": messages,
       "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
       ...
   })
   text = resp.json()["choices"][0]["message"]["content"]
   ```

`_strip_thinking()` always runs regardless of `ENABLE_THINKING`, so a stray `<think>` block
can never corrupt the code even if the flag is misconfigured or the model ignores it.
Non-thinking models (Qwen2.5-Coder, etc.) produce no `<think>` blocks and are unaffected.

### Task-type few-shot

The generation prompt injects one concise in-context example matched to the spec's `type:`
field: `function` → a `clamp`, `class` → a `Counter`, `module` → constants+exception+function,
`connected functions` → cooperating helpers (`slugify`), `project` → a dataclass + file-writing
class. These are local-only tokens (zero cloud cost) and use domains deliberately unrelated to
the five benchmark tasks, so an example teaches output style (type hints, edge-case raising, no
prose) without ever leaking an answer. Few-shot is Python-only; a Dockerfile/YAML/JSON spec gets
the matching per-language rules instead.

---

## Step 6 — The benchmark system (`experiments/bench_real.py`)

### The backends (v2)

| Backend | Cloud cost | What runs |
|---|---|---|
| `local_vllm` | **$0** | vLLM: conditions → tests → code → fix loop, fully offline |
| `local_ollama` | **$0** | Same, via Ollama (any GGUF model) |
| `local_vllm_rescue` | $0 or one call | `local_vllm` + a single Claude fix **only if** the local model fails |
| `local_ollama_rescue` | $0 or one call | Ollama variant of the above |
| `claude_direct` | always | Claude writes tests + code + fixes, every iteration billed (baseline) |

This replaces the earlier `claude_mcp` design in which **Claude wrote the tests** up front
(~5,800 cloud tokens/task of fixed overhead, incurred whether or not the local model succeeded).
In v2 the local model writes the tests from `conditions:`, and Claude is touched only on failure —
so the expected cloud cost is `P(local failure) × one rescue call`, near zero for tasks the local
model handles.

### Fair quality comparison

Every backend is graded against the **same canonical acceptance tests** shipped in each spec's
`tests:` field — hand-written pytest derived from the `conditions:`. No backend writes the tests
it is judged by, so a weak model can't flatter itself with easy tests (and can't sink itself with
buggy ones). The model never sees the test source, only the failure output, so the canonical tests
provide fix-loop feedback without leaking the solution. (In *production*, where there are no
canonical tests, the local model converts `conditions:` → tests itself; that path is still used for
custom tasks that ship only `conditions:`.)

### What gets measured (CSV columns)

- `time_s` — wall clock end-to-end
- `cloud_in_tok` / `cloud_out_tok` — real token counts from the API `usage` field (never estimated)
- `local_in_tok` / `local_out_tok` — vLLM/Ollama token counts from the API `usage` field
- `cost_usd` — from `CLAUDE_INPUT_PRICE` / `CLAUDE_OUTPUT_PRICE` (default Haiku 4.5: $0.80/M in, $4.00/M out)
- `syntax_ok` / `exec_ok` / `tests_ok` — which gates passed · `confidence` (0/33/67/100)
- `fix_iters` — fix cycles before passing or giving up · `claude_rescue` — was the fallback used

---

## Results

### v2 results — RTX 4050 Laptop (6 GB VRAM) · canonical tests · Claude Haiku 4.5 (2026-07-02)

Overall confidence = mean of the per-task 3-gate score (syntax 33 · exec 67 · tests 100) across
all five tasks. "Pass" counts tasks reaching all three gates. Rescue cost is the total cloud spend
for the whole 5-task run (Claude is called only for tasks the local model fails).

| Config (context) | Local only | Local cost | With rescue | Rescue cost | Time (local→rescue) |
|---|---|---|---|---|---|
| **Qwen3-4B-AWQ** (8K) | **93% · 4/5** | $0 | **100% · 5/5** | **$0.0036** | 130s → 140s |
| Qwen2.5-Coder-3B-AWQ (24K) | 80% · 2/5 | $0 | 100% · 5/5 | $0.0117 | 278s → 305s |
| Qwen2.5-Coder-1.5B-AWQ (32K) | 80% · 3/5 | $0 | 93% · 4/5 | $0.0068 | 108s → 119s |
| Claude Haiku 4.5 (`claude_direct`) | — | — | 100% · 5/5 | **$0.0221** | 36s |

Reproduce with `make exp-bench-full` (and `experiments/run_models.sh` for the model sweep). See
[`experiments/experiments.md`](../experiments/experiments.md) for per-task tables and token counts.

### What the numbers show

- **A 4B local model matches the cloud baseline's quality at 6× lower cost.** Qwen3-4B-AWQ solves
  4/5 tasks offline for free; Claude rescues only the 1 it misses (`connected`), so the whole run
  costs **$0.0036 vs $0.0221** for `claude_direct` — both at 100%, all tests passing.
- **Rescue economics hold as predicted.** Expected cloud cost = fallback_rate × one rescue call.
  Qwen3-4B fell back on 1/5 tasks; Coder-3B on 3/5 ($0.0117). The weaker the local model, the more
  rescues fire, and cost rises smoothly toward — but stays well under — the cloud baseline.
- **Small models are more capable than expected on well-specified tasks.** Even Qwen2.5-Coder-1.5B
  clears `class`, `connected`, and `module` unaided (3/5, free). The nested-list `function` and the
  multi-part `project` are what defeat the smallest models even after a rescue.
- **Context is GQA-bound, not size-bound.** The 1.5B and 3B (2 KV heads) run at 32K/24K on 6 GB; the
  4B (8 KV heads) is capped near 8K. Bigger parameters bought *less* context here, not more.
- **Cloud is fastest** (36s vs 108–305s): the local win is cost, not latency. Coder-3B is slowest
  because it enters the 6-fix loop on 3 tasks; Qwen3-4B one-shots four and finishes in 130s.

### Thinking mode (extended reasoning) — local vs cloud

Every thinking-capable config was benchmarked with reasoning off and on (Qwen2.5-Coder has no
`<think>` mode). Both cloud (`thinking` param) and local (`enable_thinking` / native `think`) are
supported in `bench_real.py`, recorded in the `thinking` CSV column.

| Config | OFF | ON | Takeaway |
|---|---|---|---|
| Claude Haiku 4.5 (cloud) | 100% · $0.0221 | 100% · $0.0262 | no quality gain, **+19% cost** |
| Qwen3-4B-AWQ (vLLM, local) | 93% · 4/5 · $0 | **100% · 5/5 · $0** | closes `connected` → **cloud parity, free** |
| qwen3.5:4b (Ollama, +rescue) | 93% · 4/5 · $0.0213 | **100% · 5/5 · $0.0106** | +1 pass at **half the cost** |

- **Turn thinking off for the cloud model** — Haiku already one-shots these tasks, so reasoning
  only burns tokens. **Turn it on for local models:** it lifts a 4B to cloud-parity for free, and
  makes a weaker model both better and cheaper (fewer, easier rescues).
- **Caveat:** thinking on Qwen3-4B/vLLM is flaky at its 8192 context ceiling on 6 GB — long traces
  pressure the KV cache and some requests 400. Keep `MAX_TOKENS ≤ 4096` there, or use a bigger GPU.

### Cross-engine: Ollama vs vLLM

The same tasks were run through Ollama (GGUF Q4). For the coder models the pass rate matches vLLM
(coder:3b → 100% with rescue for $0.0153, vs vLLM's $0.0117) — **engine choice is a latency/ops
decision, not a quality one.** The context matrix (largest that fits 6 GB, identical for both
thinking modes) and full per-model numbers are in
[`experiments/experiments.md`](../experiments/experiments.md) §4.2–4.4.

---

## Lessons learned

**1. Models ignore formatting instructions — and chatty ones wrap prose *around* the fence.**
Always strip markdown fences at the call layer. A boundary-only strip is not enough: instruct
models like Qwen2.5-Coder-3B say "Certainly! Below is…```python…```…This works by…", so the
extractor must pull the fenced block out of the surrounding prose, not just trim the ends. Missing
this made every chatty-model result read as a syntax failure — a pure harness artifact.

**2. The fix loop must be stateful and see the full error.**
An early version issued each fix as a fresh one-shot (`code + truncated error → new code`) with no
memory of prior attempts. Models oscillated — fix A, break B, revert A — burning tokens without
converging, which looked like a "capability gap." Keeping one growing conversation and feeding the
*full* failure text (not a 800-char cut) let both local models and Claude converge in far fewer
iterations. The loop is also best-of: the highest gate reached is kept, so a late regressing fix
can't lower the score.

**3. Grade every backend on the same canonical tests.**
If each backend generates the tests it is judged by, a weak test-writer poisons the score (nothing
passes its buggy tests) and the comparison is meaningless. Each spec now ships hand-written
canonical `tests:`; all backends are graded against those identical tests. This alone turned a
misleading "Claude fails `class`/`connected`" result into the true 5/5. Self-generated tests remain
only for production/custom tasks that ship just `conditions:`.

> Lessons 1–3 were all discovered *because* a fair re-run produced implausible results (a 4B model
> beating Claude, Claude failing an LRU cache). When a benchmark surprises you, suspect the harness
> before the model.

**4. SSE health checks need special handling.**
`curl --max-time 5 -sf <sse-url>` exits with code 28 (timeout) even on a healthy SSE stream.
Use `-w '%{http_code}'` and check if the HTTP code equals `200` separately.

**5. Two start modes are non-negotiable.**
Production mode (`make start`): port 8001 closed. Bench mode (`make start-bench`): port 8001 open.
The `health-check` target is a prerequisite of all `exp-bench-*` targets so this can't be skipped.

**6. No pip on the host.**
All experiment Python packages live in `dockeduck-experiments` Docker image.
Repo mounted at `/repo`, network mode `host` for vLLM access.

**7. Usable context is set by GQA KV-heads, not parameter count.**
Qwen2.5-Coder-1.5B/3B (2 KV heads) run at 32K/24K on 6 GB; Qwen3-4B (8 KV heads) needs 4× the KV
cache per token and OOMs above ~8K. Size up the model and you may have to size *down* the context.

**3. SSE health checks need special handling.**
`curl --max-time 5 -sf <sse-url>` exits with code 28 (timeout) even on a healthy SSE stream.
Use `-w '%{http_code}'` and check if the HTTP code equals `200` separately.

**4. Two start modes are non-negotiable.**
Production mode (`make start`): port 8001 closed. Bench mode (`make start-bench`): port 8001 open.
The `health-check` target is a prerequisite of all `exp-bench-*` targets so this can't be skipped.

**5. No pip on the host.**
All experiment Python packages live in `dockeduck-experiments` Docker image.
Repo mounted at `/repo`, network mode `host` for vLLM access.

**6. The capability ceiling is the model, not the architecture.**
The hardest tasks fail with a 3B model regardless of backend.
Swap `VLM_MODEL` (or `OLLAMA_MODEL`) to a 7B/14B model to push the ceiling up.

---

## File map

```
templates/04-vllm-mcp-coder/         # vLLM backend
├── Dockerfile             # non-root build, vLLM + FastMCP
├── entrypoint.sh          # starts vLLM + MCP, waits for vLLM ready
├── requirements.txt       # mcp, httpx, pyyaml, pytest
├── Makefile               # container lifecycle
├── .env.example           # all variables with defaults
└── src/
    ├── server.py          # 3 MCP tools: write_input_file, write_and_fix, validate_output_file
    ├── vlm.py             # CoderClient: HTTP → vLLM /v1 + _strip_fences/_strip_thinking
    ├── validator.py       # check_syntax, run_code, run_tests
    └── prompts.py         # language-aware, task-type few-shot prompt builders

templates/05-ollama-mcp-coder/       # Ollama backend (docker compose; native /api/chat)

experiments/
├── bench_real.py          # v2 benchmark: local / local+rescue / claude_direct
├── custom_task.py         # single-spec runner (thin driver over bench_real)
├── mcp_experiments.py     # dry token/context estimators
├── small_context_experiments.py
├── Dockerfile             # lightweight: anthropic + httpx + pytest + pyyaml
├── tasks/                 # one spec per task type (conditions: + description)
│   ├── function-example.yaml           # function
│   ├── class-example.yaml              # class
│   ├── connect-functions-example.yaml  # connected functions
│   ├── module-example.yaml             # module
│   └── project-example.yaml            # project (hardest)
└── results/               # CSV outputs from benchmark runs (gitignored)

Makefile (repo root)       # start-bench/start-ollama, exp-build, exp-bench-*, exp-try, health-*
```
