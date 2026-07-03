# DockeDuck — Local LLM Benchmark

**Hardware:** NVIDIA RTX 4050 Laptop GPU (6 GB VRAM) · 32 GB RAM · **Cloud baseline:** Claude Haiku 4.5

> Current figures only, measured 2026-07 under the corrected harness (canonical acceptance tests,
> stateful best-of fix loop, fenced-block extraction — see [§5](#5-the-harness-not-the-model-was-the-bottleneck)).
> Plain-language summary: [`RESULTS.md`](RESULTS.md). Architecture: [`../docs/mcp_technology.md`](../docs/mcp_technology.md).
> Reproduce: `bash experiments/run_models.sh` (vLLM sweep) · `bash experiments/run_thinking_ollama.sh`
> (thinking + Ollama) — see [§6](#6-reproducibility).

---

## 1. How it works

The user writes a YAML spec (`conditions:` in natural language, or hand-written `tests:`). The
local model turns conditions into pytest, generates code, and runs a syntax → exec → tests fix
loop — **entirely offline, $0 cloud**. Claude is called only to *rescue* a task the local model
can't finish after N retries.

```
User YAML (conditions)
      │
      ▼
Local model: conditions → tests → code → fix loop        ($0 cloud)
      │
      ├── all tests pass → done, $0
      └── still failing after N retries → one Claude rescue call (~$0.01–0.03)
```

**Expected cloud cost = fallback_rate × one rescue** → near-zero for tasks the local model handles.
We evaluate correctness (a 3-gate pass rate), speed, cloud cost, and the largest context that fits.

---

## 2. Tasks

Five tasks of increasing complexity, each a spec in `experiments/tasks/` with natural-language
`conditions:` **and** a hand-written canonical `tests:` block (the shared ground truth all
backends are graded against).

| Key | Complexity | Spec / file | Description |
|---|---|---|---|
| `function`  | ⭐ | `function-example.yaml` → `find_item.py` | Linear search, flat or one-level-nested list |
| `class`     | ⭐⭐ | `class-example.yaml` → `lru_cache.py` | LRU cache, O(1) get/put via OrderedDict |
| `connected` | ⭐⭐⭐ | `connect-functions-example.yaml` → `file_search.py` | Three chained functions: open → read → search |
| `module`    | ⭐⭐⭐⭐ | `module-example.yaml` → `config_loader.py` | ConfigLoader/Schema/Error + env vars + file I/O |
| `project`   | ⭐⭐⭐⭐⭐ | `project-example.yaml` → `project_scaffolder.py` | Dataclass + YAML + file I/O + string generation |

---

## 3. Scoring & backends

**3-gate score** (per task): syntax parses = 33 · runs = 67 · all canonical tests pass = 100.
**Confidence** = mean across the 5 tasks. **Pass** = tasks reaching all three gates.

| Backend | Cloud cost | What runs |
|---|---|---|
| `local_vllm` | **$0** | vLLM: conditions → tests → code → fix loop, fully offline |
| `local_ollama` | **$0** | Same, via Ollama (any GGUF model) |
| `local_vllm_rescue` | $0 or one call | `local_vllm` + a single Claude fix **only if** the local model fails |
| `local_ollama_rescue` | $0 or one call | Ollama variant of the above |
| `claude_direct` | always | Claude writes tests + code + fixes, every iteration billed (baseline) |

> vLLM and Ollama cannot share the 6 GB GPU — the Ollama runs stop the vLLM container first.

---

## 4. Results

### 4.1 vLLM model sweep (AWQ 4-bit)

`--quantization awq --enforce-eager`, util 0.88. Produced by `experiments/run_models.sh`.

| Config | Context | Backend | Confidence | Pass | Cloud cost | Time |
|---|---|---|---|---|---|---|
| Qwen2.5-Coder-1.5B-AWQ | 32 768 | local | 80.0% | 3/5 | $0 | 108s |
| Qwen2.5-Coder-1.5B-AWQ | 32 768 | + rescue | 93.4% | 4/5 | $0.0068 | 119s |
| Qwen2.5-Coder-3B-AWQ | 24 576 | local | 80.2% | 2/5 | $0 | 278s |
| Qwen2.5-Coder-3B-AWQ | 24 576 | + rescue | **100%** | 5/5 | $0.0117 | 305s |
| **Qwen3-4B-AWQ** | 8 192 | local | **93.4%** | 4/5 | $0 | 130s |
| **Qwen3-4B-AWQ** | 8 192 | **+ rescue** | **100%** | 5/5 | **$0.0036** | 140s |
| Claude Haiku 4.5 | — | claude_direct | 100% | 5/5 | $0.0221 | 36s |

**Per-task quality** (local → after rescue; ✓✓✓ tests · ✓✓✗ exec · ✓✗✗ syntax):

| Task | Coder-1.5B | Coder-3B | Qwen3-4B | claude_direct |
|---|---|---|---|---|
| function | ✓✓✗ → ✓✓✗ | ✓✓✗ → ✓✓✓ | ✓✓✓ | ✓✓✓ |
| class | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓✓ |
| connected | ✓✓✓ | ✓✓✓ | ✓✓✗ → ✓✓✓ | ✓✓✓ |
| module | ✓✓✓ | ✓✓✗ → ✓✓✓ | ✓✓✓ | ✓✓✓ |
| project | ✓✗✗ → ✓✓✓ | ✓✓✗ → ✓✓✓ | ✓✓✓ | ✓✓✓ |
| rescues fired | 2/5 | 3/5 | 1/5 | — |

**Headline:** **Qwen3-4B-AWQ reaches the cloud baseline's 100% / 5-pass at $0.0036 vs $0.0221 —
6.1× cheaper** — solving 4/5 offline and paying Claude only to rescue `connected`. Coder-3B rescue
also hits 100% ($0.0117, 1.9× cheaper). Even the 1.5B clears 3/5 unaided, and `claude_direct` is a
clean 5/5 baseline at $0.0221.

### 4.2 Ollama sweep (GGUF Q4)

Same tasks/grading; Ollama serves GGUF and swaps models on demand. `experiments/run_thinking_ollama.sh`.

| Model (context) | local conf | pass | local time | + rescue | pass | rescue cost |
|---|---|---|---|---|---|---|
| qwen2.5-coder:1.5b (16K) | 66.8% | 1/5 | 102s | 100% | 5/5 | $0.0144 |
| qwen2.5-coder:3b (16K) | 80.2% | 2/5 | 176s | 100% | 5/5 | $0.0153 |
| qwen3.5:4b (12K) | 66.6% | 2/5 | 3543s | 93.4% | 4/5 | $0.0213 |

- **Ollama matches vLLM on quality** for the coder models — coder:3b reaches 100% with rescue
  ($0.0153), mirroring the vLLM Coder-3B result ($0.0117). Engine choice is a **latency/ops**
  decision, not a quality one (Ollama/llama.cpp is snappier per request; vLLM/AWQ gives more context).
- **`qwen3.5:4b` is the outlier:** slow (~59 min for 5 tasks — it emits long outputs even with
  `think:false`) and weaker unaided; turning **thinking on** fixes both (§4.3).

### 4.3 Thinking mode — local vs cloud

Each thinking-capable config run reasoning **off** and **on** at the same context (output budget
raised for thinking so the `<think>` trace + code fit). Qwen2.5-Coder has no `<think>` mode.

| Config | Thinking OFF | Thinking ON | Effect |
|---|---|---|---|
| Claude Haiku 4.5 (cloud) | 100% · 5/5 · $0.0221 | 100% · 5/5 · $0.0262 | **none; +19% cost** |
| Qwen3-4B-AWQ (vLLM, local) | 93% · 4/5 · $0 | **100% · 5/5 · $0** | **+`connected` → cloud parity, free** |
| qwen3.5:4b (Ollama, local) | 66.6% · 2/5 | **86.8% · 3/5** | **+20 pts** (`function`/`project` improve) |
| qwen3.5:4b (Ollama, + rescue) | 93.4% · 4/5 · $0.0213 | **100% · 5/5 · $0.0106** | **+1 pass, half the cost** |

- **Thinking helps local models, not the cloud one.** Haiku already one-shots these tasks, so
  reasoning only adds tokens (+19% cost, no gain) — **leave it off for the cloud**.
- **On a capable local model it buys cloud-parity for free:** Qwen3-4B 4/5 → 5/5, no rescue needed.
- **On a weaker model it lifts quality *and* cuts rescue cost** (fewer, easier rescues).
- **Caveat:** Qwen3-4B thinking on vLLM is flaky at its 8192 ceiling on 6 GB — long traces pressure
  the KV cache and some requests return HTTP 400. Keep `MAX_TOKENS ≤ 4096` there, or use a bigger GPU.

### 4.4 Context-window matrix

Context = the **largest that fits 6 GB per model**, identical for both thinking modes (a VRAM/KV
constraint). vLLM context is **KV-head-bound**: the 8-head 4B holds far less than the 2-head coders.
Only the output budget `MAX_TOKENS` changes with thinking.

| Framework | Model | Context (both modes) | MAX_TOKENS off / on |
|---|---|---|---|
| vLLM | Qwen2.5-Coder-1.5B-AWQ (2 KV heads) | 32768 | 4096 / — |
| vLLM | Qwen2.5-Coder-3B-AWQ (2 KV heads) | 24576 | 4096 / — |
| vLLM | Qwen3-4B-AWQ (8 KV heads) | 8192 | 4096 / 4096 |
| Ollama | qwen2.5-coder:1.5b / :3b | 16384 | 4096 / — |
| Ollama | qwen3.5:4b | 12288 | 4096 / 6144 |
| Cloud | Claude Haiku 4.5 | 200K | — / thinking budget 1500 |

The `recommend_model` / `recommend_context_window` MCP tools compute these for *your* GPU.

---

## 5. The harness, not the model, was the bottleneck

An early run showed `claude_direct` (Haiku) *failing* the `class` task — SYNTAX OK ✓✗✗ after 5
iterations and $0.06 — while a 4B local model "beat" it. Neither is credible: Haiku can write an LRU
cache, and a 4B model does not out-code Haiku. Treating the surprise as a harness signal, we found
**three** defects, each corrupting *every* backend:

1. **Fenced-block extraction.** Chatty instruct models return
   "Certainly! Below is…\n```python\n<code>\n```\nThis works by…". The old strip only trimmed fences
   at the *ends*, leaving the prose in place, so the "code" failed to parse — every chatty-model task
   read as `✗✗✗`. Fixed: extract the fenced block out of the surrounding prose.
2. **Stateless, truncated fix loop.** Each fix was a fresh one-shot (`code + 800-char error → new
   code`) with no memory. Models oscillated (fix A → break B → revert A), burning tokens without
   converging. Fixed: one **stateful** conversation + **full** error text + **best-of** selection.
3. **Self-graded tests.** Each backend generated the pytest it was judged by, so a weak test-writer
   poisoned its own score. Fixed: each spec ships **canonical `tests:`**; every backend is graded on
   those identical tests.

After all three fixes the picture inverted to something sensible (§4): `claude_direct` is a clean
5/5 at $0.022, local models show a real capability gradient, and rescue reaches 100% at a fraction
of cloud cost.

**Lesson:** before concluding anything about a *model*, verify the *harness*. Nearly the entire
apparent "capability gap" was fence prose, an oscillating fix loop, and backends grading themselves.

---

## 6. Reproducibility

CSVs land in `experiments/results/` (gitignored). No host pip installs — everything runs in the
`dockeduck-experiments` image against the repo mounted at `/repo`.

```bash
# One-time
cp templates/04-vllm-mcp-coder/.env.example templates/04-vllm-mcp-coder/.env   # add ANTHROPIC_API_KEY for rescue/baseline
make -C templates/04-vllm-mcp-coder build
make exp-build

# vLLM: full model sweep (claude_direct once, then each model at its max-fit context)
bash experiments/run_models.sh                 # → results/bench_<model>.csv

# Thinking-mode + Ollama sweep (vLLM first, then frees the GPU for Ollama)
bash experiments/run_thinking_ollama.sh        # → results/bench_*think*.csv, bench_ollama_*.csv

# Single ad-hoc runs
make start-bench && make wait-ready
make exp-bench-full                            # local_vllm + rescue + claude_direct, all tasks
make exp-try FRAMEWORK=vllm   TASK=project THINKING=true
make exp-try FRAMEWORK=ollama MODEL=qwen2.5-coder:3b CTX=16384 TASK=connected
```

Knobs: `VLM_MODEL` / `VLM_MAX_MODEL_LEN` (template 04 `.env`) or `OLLAMA_MODEL` / `OLLAMA_NUM_CTX`
(template 05); `ENABLE_THINKING`, `MAX_TOKENS`; cloud model + pricing via
`CLAUDE_MODEL` / `CLAUDE_INPUT_PRICE` / `CLAUDE_OUTPUT_PRICE` (default Haiku 4.5); `CLAUDE_THINKING`
for cloud reasoning. The `run_*.sh` scripts also demonstrate the per-model context sizing and the
OOM-retry-at-half-context fallback.
