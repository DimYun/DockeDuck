# DockeDuck — Local LLM Benchmark Experiments
**Hardware:** NVIDIA GeForce RTX 4050 Laptop GPU (6 GB VRAM) · 32 GB RAM

> **Status — the current figures are in [§6.6](#66-canonical-v2-run--model-sweep-2026-07-02--current-figures)–6.9.**
> §6.6 is the canonical July 2026 vLLM sweep (Qwen2.5-Coder-1.5B/3B-AWQ, Qwen3-4B-AWQ) vs Claude
> Haiku 4.5; §6.7 adds the **Ollama** GGUF sweep, §6.8 the **thinking-mode** comparison (local vs
> cloud), §6.9 the **context-window matrix**. All graded on hand-written canonical `tests:` under
> the corrected harness (stateful fix loop, fenced-block extraction, identical tests per backend —
> see [§10.6](#106-the-harness-not-the-model-was-the-bottleneck-methodology-fix)).
> **Sections 6.1–6.5, 7.x, and 8.x are a superseded methodology record** (each backend wrote its own
> tests; stateless loop; boundary-only strip) and their numbers are not comparable. Regenerate the
> sweep with `experiments/run_models.sh`; single runs with `make exp-bench-full` / `make exp-bench-ollama`
> (see [§13](#13-reproducibility)). Cloud baseline defaults to **Claude Haiku 4.5** (`CLAUDE_MODEL`).
>
> **Models exercised (historical, §6.1–6.5):** Qwen2.5-Coder (3B/7B AWQ+GGUF), Qwen3-4B-AWQ,
> Ministral (`mistral:3b`), "OmniCoder" (mapped to `qwen2.5-coder:7b`), bare Claude.

---

## 0. Architecture Evolution

### v1 (initial benchmark) — Claude writes the tests

```
User (English) → Claude → write_input_file (Claude writes pytest, ~5 800 cloud tokens)
                       → write_and_fix (local model generates code)
```
Cloud cost per task: ~$0.04 fixed regardless of whether local model succeeds.
Result: MCP was 5.2× *more* expensive than pure Claude because Claude was doing the
expensive test-writing step on every task.

### v2 (current) — User writes the YAML, local model is primary

```
User writes YAML (conditions: in natural language)
       │
       ▼
Local model: conditions → pytest tests → code → fix loop   ($0 cloud)
       │
       ├── success → done, $0 cost
       │
       └── failure after N retries → Claude rescue (one call, ~$0.01–0.03)
```

Cloud cost per task: 0 if local model succeeds · rescue cost only on failure.
Expected cloud cost = fallback_rate × ~$0.02 per task.

**Key insight:** with well-specified YAML conditions, the cloud cost is:
- v1 MCP: always ~$0.04/task
- v2 rescue: P(fail) × ~$0.02/task → near-zero for tasks local model handles

---

## 1. Goal

Compare four local-model configurations against Claude Sonnet 4.6 as a cloud baseline,
across five code-generation tasks of increasing complexity.  
Evaluate: correctness (3-gate pass rate), speed, cloud token cost, and maximum usable
context window on the RTX 4050.

---

## 2. Models Tested

### 2.1 Requested vs Available

| Requested by user | Status | Actual model tested | Reason |
|---|---|---|---|
| Qwen2.5-Coder-3B-AWQ | ✅ Direct | `Qwen/Qwen2.5-Coder-3B-Instruct-AWQ` via vLLM | Exact match — already in 04-vllm-mcp-coder image |
| Qwen3.5-4B | ⚠️ Substituted | Pending Ollama update | `qwen3:4b` requires Ollama ≥ 0.7.x; installed: 0.6.1. Run `! curl -fsSL https://ollama.com/install.sh | sh` to update. |
| Ministral-3B / Ministral-8B | ⚠️ Substituted | `mistral:7b-instruct-q4_K_M` via Ollama | Ministral-3B/8B not published to Ollama registry. Closest available: Mistral 7B (same Mistral family, Q4_K_M). For true Ministral via vLLM: `mistralai/Ministral-3B-Instruct` on HuggingFace. |
| OmniCoder-9B | ⚠️ Substituted | `qwen2.5-coder:7b` via Ollama | No public "OmniCoder-9B" model found in Ollama or HuggingFace. Substituted with Qwen2.5-Coder-7B — the best coding model in the 7-9B range available on this GPU. |

### 2.2 Final Model Matrix

| # | Model | Engine | Quantization | VRAM measured | Context tested | Max possible ctx | Disk |
|---|---|---|---|---|---|---|---|
| M1 | Qwen2.5-Coder-3B-AWQ | vLLM (template 04) | AWQ 4-bit | **2.5 GB** @ ctx=16384 | 16 384 tok | **32 768 tok** | HF cache |
| M2 | Qwen2.5-Coder-3B-Q4 | Ollama (template 05) | GGUF Q4_K_M | **2.5 GB** @ ctx=16384 | 16 384 tok | **32 768 tok** | ExternalData |
| M3 | Mistral-7B-Instruct-Q4 | Ollama (template 05) | GGUF Q4_K_M | **4.6 GB** @ ctx=8192 | 8 192 tok | **24 576 tok** | ExternalData |
| M4 | Qwen2.5-Coder-7B-Q4 | Ollama (template 05) | GGUF Q4_K_M | **4.6 GB** @ ctx=8192 | 8 192 tok | **24 576 tok** | ExternalData |
| REF | Claude Sonnet 4.6 | Anthropic API | — | — | — | 200 000 tok | — |

---

## 3. Context Window Calculation

### 3.1 Actual VRAM measurements (GPU-Z / nvidia-smi during experiment)

| Model | VRAM @ tested ctx | Model weights | KV cache used | KV cache per token |
|---|---|---|---|---|
| Qwen2.5-Coder-3B (any quant) | **2.5 GB** @ ctx=16384 | ~2.0 GB | ~0.5 GB (512 MB) | **32 KB/tok** |
| Mistral-7B Q4_K_M | **4.6 GB** @ ctx=8192 | ~4.1 GB | ~0.5 GB (512 MB) | **64 KB/tok** |
| Qwen2.5-Coder-7B Q4_K_M | **4.6 GB** @ ctx=8192 | ~4.1 GB | ~0.5 GB (512 MB) | **64 KB/tok** |

### 3.2 Maximum context window formula

```
Max KV budget = 6 GB total - model weights
Max context   = Max KV budget / KV cache per token
```

| Model | Max KV budget | KV/tok | Theoretical max | Model RoPE limit | **Recommended max** |
|---|---|---|---|---|---|
| Qwen2.5-Coder-3B | 4.0 GB | 32 KB | 131 072 tok | 32 768 tok | **32 768** |
| Mistral-7B | 1.9 GB | 64 KB | 31 130 tok | 32 768 tok | **24 576** |
| Qwen2.5-Coder-7B | 1.9 GB | 64 KB | 31 130 tok | 32 768 tok | **24 576** |

> **Key correction:** The experiments used conservative context settings. The 7B models can
> actually handle **3× more context** than tested (24576 vs 8192). Re-running with these
> settings would benefit RAG tasks and long fix-loop conversations. The 3B models are already
> near their RoPE ceiling at 16384 but could go to 32768 with no extra VRAM cost.

> **AWQ vs Q4_K_M precision:** AWQ keeps precision higher than GGUF Q4_K_M for the same VRAM
> footprint — this explains the quality gap between M1 and M2 despite identical 2.5 GB usage.

---

## 4. Tasks

Five tasks of increasing complexity, each with a spec YAML in `experiments/tasks/`
(natural-language `conditions:` + description). Registry keys mirror the task `type:`.

| Key | Complexity | Spec / file | Description |
|---|---|---|---|
| `function`  | ⭐ | `function-example.yaml` → `find_item.py` | Linear search, flat or one-level-nested list |
| `class`     | ⭐⭐ | `class-example.yaml` → `lru_cache.py` | LRU cache, O(1) get/put via OrderedDict |
| `connected` | ⭐⭐⭐ | `connect-functions-example.yaml` → `file_search.py` | Three chained functions: open → read → search |
| `module`    | ⭐⭐⭐⭐ | `module-example.yaml` → `config_loader.py` | ConfigLoader/Schema/Error + env vars + file I/O |
| `project`   | ⭐⭐⭐⭐⭐ | `project-example.yaml` → `project_scaffolder.py` | Dataclass + YAML + file I/O + string gen |

> Historical result tables further down still use the earlier keys `file` (→ `connected`) and
> `complex` (→ `project`), and the old filenames `binary_search.py` / `task_queue.py`. The specs
> themselves have since been renamed to the above.

---

## 5. Backends / Test Scenarios

### v2 backends (current)

| Backend | Cloud cost | Description |
|---|---|---|
| `local_vllm` | **$0** | User YAML → local model (conditions→tests→code fix loop via vLLM) |
| `local_ollama` | **$0** | Same but via Ollama (any GGUF model) |
| `local_vllm_rescue` | $0 or ~$0.02 | Same as `local_vllm` + Claude called only on failure |
| `local_ollama_rescue` | $0 or ~$0.02 | Same as `local_ollama` + Claude called only on failure |
| `claude_direct` | always ~$0.01–0.05 | Claude writes tests + code + fixes; baseline |

### v1 backends (phase 1/2 experiments below used these)

| Backend | Cloud cost | Description |
|---|---|---|
| `local_vlm` (v1) | **$0** | Pre-written spec + tests → local model via vLLM |
| `ollama_local` (v1) | **$0** | Pre-written spec + tests → local model via Ollama |
| `claude_mcp` (v1) | ~$0.04 fixed | Claude writes tests (~5 800 tokens), local model does code |
| `claude_direct` (v1) | ~$0.002–0.017 | Claude generates code from compact spec prompt |

> **VRAM constraint:** vLLM and Ollama cannot share the 6 GB GPU.
> v1 experiments ran in phases: Phase 1 (claude_direct), Phase 2 (vLLM), Phase 3 (Ollama).

---

## 6. Raw Results

### 6.1 Claude Sonnet 4.6 — cloud baseline

| Task | Time | Cld-In | Cld-Out | Cost USD | Quality |
|---|---|---|---|---|---|
| function | 9.9s | 67 | 115 | $0.00193 | ✓✓✓ TESTS PASS |
| class | 4.6s | 121 | 189 | $0.00320 | ✓✓✓ TESTS PASS |
| file | 6.5s | 162 | 408 | $0.00661 | ✓✓✓ TESTS PASS |
| module | 12.8s | 192 | 1 063 | $0.01652 | ✓✓✓ TESTS PASS |
| complex | 6.5s | 500 | 575 | $0.01013 | ✓✓✓ TESTS PASS |
| **Total** | **40.3s** | **1 042** | **2 350** | **$0.03839** | **5/5** |

### 6.2 M1 — Qwen2.5-Coder-3B-AWQ via vLLM (ctx=16 384)

#### local_vlm backend (no cloud)
| Task | Time | Lcl-In | Lcl-Out | Fix iters | Quality |
|---|---|---|---|---|---|
| function | 36.0s | 340 | 290 | 1 | ✓✓✓ TESTS PASS |
| class | 16.2s | 141 | 133 | 0 | ✓✓✓ TESTS PASS |
| file | 188.7s | 3 284 | 1 536 | 6 | ✓✓✗ EXEC PASS |
| module | 74.0s | 750 | 602 | 1 | ✓✓✓ TESTS PASS |
| complex | 58.0s | 452 | 473 | 0 | ✓✓✓ TESTS PASS |

#### claude_mcp backend (cloud writes spec only)
| Task | Time | Cld-In | Cld-Out | Lcl-In | Lcl-Out | Cost USD | Quality |
|---|---|---|---|---|---|---|---|
| function | 61.4s | 4 791 | 776 | 341 | 291 | $0.02601 | ✓✓✓ TESTS PASS |
| class | 46.7s | 8 668 | 1 643 | 141 | 133 | $0.05065 | ✓✓✓ TESTS PASS |
| file | 702.2s | 4 247 | 1 717 | 5 055 | 5 554 | $0.03850 | ✓✓✗ EXEC PASS |
| module | 291.7s | 4 521 | 1 724 | 2 539 | 2 177 | $0.03942 | ✓✓✗ EXEC PASS |
| complex | 198.5s | 7 060 | 1 708 | 1 026 | 1 449 | $0.04680 | ✓✓✓ TESTS PASS |

### 6.3 M2 — Qwen2.5-Coder-3B-Q4_K_M via Ollama (ctx=16 384)

| Task | Time | Lcl-In | Lcl-Out | Fix iters | Quality |
|---|---|---|---|---|---|
| function | 12.0s | 96 | 112 | 0 | ✓✓✓ TESTS PASS |
| class | 2.8s | 141 | 134 | 0 | ✓✓✓ TESTS PASS |
| file | 33.5s | 3 491 | 1 646 | 6 | ✓✓✗ EXEC PASS |
| module | 61.1s | 3 850 | 3 207 | 6 | ✓✓✗ EXEC PASS |
| complex | 69.4s | 4 889 | 3 516 | 6 | ✓✓✗ EXEC PASS |

### 6.4 M3 — Mistral-7B-Instruct-Q4_K_M via Ollama (ctx=8 192)

| Task | Time | Lcl-In | Lcl-Out | Fix iters | Quality |
|---|---|---|---|---|---|
| function | 14.1s | 380 | 246 | 1 | ✓✓✓ TESTS PASS |
| class | 45.6s | 4 279 | 1 240 | 6 | ✓✓✗ EXEC PASS |
| file | 78.5s | 5 057 | 2 209 | 6 | ✓✓✗ EXEC PASS |
| module | 125.6s | 4 396 | 3 771 | 6 | ✓✗✗ SYNTAX OK |
| complex | 124.7s | 4 417 | 3 752 | 6 | ✗✗✗ FAILED |

### 6.5 M4 — Qwen2.5-Coder-7B via Ollama (ctx=8 192)

| Task | Time | Lcl-In | Lcl-Out | Fix iters | Quality |
|---|---|---|---|---|---|
| function | 10.9s | 96 | 106 | 0 | ✓✓✓ TESTS PASS |
| class | 4.9s | 141 | 135 | 0 | ✓✓✓ TESTS PASS |
| file | 63.6s | 3 489 | 1 760 | 6 | ✓✓✗ EXEC PASS |
| module | 92.7s | 3 717 | 2 624 | 6 | ✓✓✗ EXEC PASS |
| complex | 30.5s | 1 041 | 840 | 1 | ✓✓✓ TESTS PASS |

### 6.6 Canonical v2 run — model sweep (2026-07-02) ★ current figures

Hardware: RTX 4050 Laptop, 6 GB VRAM · `--quantization awq --enforce-eager`, util 0.88.
Grading: every backend scored on the **canonical `tests:`** in each spec (identical across
backends). Cloud baseline: Claude Haiku 4.5 ($0.80/M in, $4.00/M out). Produced by
`experiments/run_models.sh` (per-model CSVs in `experiments/results/`).

Confidence = mean per-task 3-gate score (syntax 33 · exec 67 · tests 100). "Pass" = all 3 gates.

#### Summary

| Config | Context | Backend | Confidence | Pass | Cloud cost | Time |
|---|---|---|---|---|---|---|
| Qwen2.5-Coder-1.5B-AWQ | 32 768 | local | 80.0% | 3/5 | $0 | 108s |
| Qwen2.5-Coder-1.5B-AWQ | 32 768 | + rescue | 93.4% | 4/5 | $0.0068 | 119s |
| Qwen2.5-Coder-3B-AWQ | 24 576 | local | 80.2% | 2/5 | $0 | 278s |
| Qwen2.5-Coder-3B-AWQ | 24 576 | + rescue | **100%** | 5/5 | $0.0117 | 305s |
| **Qwen3-4B-AWQ** | 8 192 | local | **93.4%** | 4/5 | $0 | 130s |
| **Qwen3-4B-AWQ** | 8 192 | **+ rescue** | **100%** | 5/5 | **$0.0036** | 140s |
| Claude Haiku 4.5 | — | claude_direct | 100% | 5/5 | $0.0221 | 36s |

#### Per-task quality matrix (local_vllm → after rescue)

Gates: ✓✓✓ tests · ✓✓✗ exec · ✓✗✗ syntax. Arrow shows the rescue lifting a failed task.

| Task | Coder-1.5B | Coder-3B | Qwen3-4B | claude_direct |
|---|---|---|---|---|
| function | ✓✓✗ → ✓✓✗ | ✓✓✗ → ✓✓✓ | ✓✓✓ | ✓✓✓ |
| class | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓✓ |
| connected | ✓✓✓ | ✓✓✓ | ✓✓✗ → ✓✓✓ | ✓✓✓ |
| module | ✓✓✓ | ✓✓✗ → ✓✓✓ | ✓✓✓ | ✓✓✓ |
| project | ✓✗✗ → ✓✓✓ | ✓✓✗ → ✓✓✓ | ✓✓✓ | ✓✓✓ |
| rescues fired | 2/5 | 3/5 | 1/5 | — |

#### Per-task detail — Qwen3-4B-AWQ (the best local config)

| Task | local quality | fixes | local tok | rescue? | rescue cost | final |
|---|---|---|---|---|---|---|
| function | ✓✓✓ | 0 | 437 | no | — | ✓✓✓ |
| class | ✓✓✓ | 0 | 469 | no | — | ✓✓✓ |
| connected | ✓✓✗ | 6 | 16 175 | yes | $0.0036 | ✓✓✓ |
| module | ✓✓✓ | 0 | 973 | no | — | ✓✓✓ |
| project | ✓✓✓ | 2 | 5 201 | no | — | ✓✓✓ |
| **Total** | **4/5** | | **23 255** | **1/5** | **$0.0036** | **5/5** |

#### claude_direct baseline (cloud only)

| Task | Quality | Fixes | Cld tok | Cost USD |
|---|---|---|---|---|
| function | ✓✓✓ | 1 | 2 943 | $0.0073 |
| class | ✓✓✓ | 0 | 1 024 | $0.0037 |
| connected | ✓✓✓ | 0 | 785 | $0.0023 |
| module | ✓✓✓ | 0 | 1 669 | $0.0054 |
| project | ✓✓✓ | 0 | 1 198 | $0.0035 |
| **Total** | **5/5** | | **7 619** | **$0.0221** |

**Headline:** Qwen3-4B-AWQ reaches the cloud baseline's 100% / 5-pass at **$0.0036 vs $0.0221 —
6.1× cheaper** — by solving 4/5 offline and paying Claude only to rescue `connected`. Coder-3B
rescue also hits 100% ($0.0117, 1.9× cheaper). Even the 1.5B clears 3/5 unaided.

> ⚠️ Sections 6.1–6.5, 7.x and the confidence tables below were measured under a **superseded
> harness** (each backend generated its own tests; stateless fix loop; boundary-only fence strip).
> Those defects — since fixed — corrupted the numbers (e.g. Claude "failing" an LRU cache). Treat
> §6.6–6.8 as the only current figures; the earlier sections are kept as a methodology record. See
> [§10.6](#106-the-harness-not-the-model-was-the-bottleneck-methodology-fix).

### 6.7 Ollama sweep — GGUF Q4 via Ollama (same tasks, canonical tests, 2026-07-03)

Cross-engine check on the same 6 GB GPU. Ollama serves GGUF Q4_K_M and swaps models on demand
(no restart per model). `local_ollama` → then `local_ollama_rescue` (Claude only on failure).
Context is the max that fits (see [§6.9](#69-context-window-matrix)); output budget 4096 (non-thinking).

| Model (context) | local conf | pass | local time | + rescue | pass | rescue cost |
|---|---|---|---|---|---|---|
| qwen2.5-coder:1.5b (16K) | 66.8% | 1/5 | 102s | 100% | 5/5 | $0.0144 |
| qwen2.5-coder:3b (16K) | 80.2% | 2/5 | 176s | 100% | 5/5 | $0.0153 |
| qwen3.5:4b (12K) | 66.6% | 2/5 | 3543s | 93.4% | 4/5 | $0.0213 |

- **Ollama matches vLLM on quality** for the coder models: coder:3b reaches 100% with rescue for
  $0.0153, mirroring the vLLM Coder-3B result ($0.0117). Same model class, same ceiling.
- **`qwen3.5:4b` is the odd one out:** slow (≈59 min for 5 tasks — it emits long outputs even with
  `think:false`) and weaker unaided (66.6%), and rescue tops out at 93.4% (project stays at exec).
  Turning **thinking on** fixes both quality and cost — see §6.8.
- **Engine choice ≈ latency, not quality.** For a coder model, Ollama (llama.cpp) and vLLM land at
  the same pass rate; pick per throughput/ops preference.

### 6.8 Thinking mode — local vs cloud (2026-07-03)

Each thinking-capable config was run with reasoning **off** and **on**, at the same context
(output budget raised for thinking: vLLM 8K-ctx uses 4096, Ollama 12K-ctx uses 6144, so the
`<think>` trace + code fit). Qwen2.5-Coder has no `<think>` mode, so it is off-only.

| Config | Thinking OFF | Thinking ON | Effect |
|---|---|---|---|
| Claude Haiku 4.5 (cloud) | 100% · 5/5 · $0.0221 | 100% · 5/5 · $0.0262 | **none; +19% cost** |
| Qwen3-4B-AWQ (vLLM, local) | 93% · 4/5 · $0 | **100% · 5/5 · $0** | **+`connected` → cloud parity, free** |
| qwen3.5:4b (Ollama, local) | 66.6% · 2/5 | **86.8% · 3/5** | **+20 pts** (`function`,`project` exec→…) |
| qwen3.5:4b (Ollama, + rescue) | 93.4% · 4/5 · $0.0213 | **100% · 5/5 · $0.0106** | **+1 pass, half the cost** |

Per-task shift (local): Qwen3-4B `connected` EXEC→TESTS (the only gap it had); qwen3.5:4b
`project` SYN→TESTS and `function` SYN→EXEC.

- **Thinking helps the local models, not the cloud one.** Haiku already solves everything in one
  shot, so reasoning only adds tokens (+19% cost, no quality change) — leave it off for cloud codegen.
- **On a capable-enough local model, thinking buys cloud-parity for free.** Qwen3-4B goes 4/5 → 5/5,
  matching `claude_direct` at $0 and needing no rescue at all.
- **On a weaker/cheaper local model, thinking lifts quality *and* lowers rescue cost** (fewer, easier
  rescues): qwen3.5:4b rescue 4/5 $0.0213 → 5/5 $0.0106.
- **Caveat (hardware ceiling):** Qwen3-4B thinking on vLLM is flaky at its 8192 context ceiling on
  6 GB — long `<think>` traces pressure the KV cache and some requests return HTTP 400. Keep
  `MAX_TOKENS ≤ 4096` at that context, or give the model more room on a larger GPU.

### 6.9 Context-window matrix

Context is set to the **largest that fits 6 GB for each model** and is **identical for both thinking
modes** (it is a VRAM/KV-cache constraint, not a mode setting). Only the output budget `MAX_TOKENS`
changes with mode. vLLM context is KV-head-bound (2-head models hold far more than the 8-head 4B).

| Framework | Model | Context (both modes) | MAX_TOKENS off / on |
|---|---|---|---|
| vLLM | Qwen2.5-Coder-1.5B-AWQ (2 KV heads) | 32768 | 4096 / — |
| vLLM | Qwen2.5-Coder-3B-AWQ (2 KV heads) | 24576 | 4096 / — |
| vLLM | Qwen3-4B-AWQ (8 KV heads) | 8192 | 4096 / 4096 |
| Ollama | qwen2.5-coder:1.5b / :3b | 16384 | 4096 / — |
| Ollama | qwen3.5:4b | 12288 | 4096 / 6144 |
| Cloud | Claude Haiku 4.5 | 200K | — / thinking budget 1500 |

---

## 7. Additional Metrics

### 7.1 Inference Throughput (local tokens / second)

Throughput = (local_in_tokens + local_out_tokens) / wall_clock_time.
Short prompts (function, class) have high startup overhead, so per-task numbers vary;
look at totals for a fair comparison.

| Task | M1 AWQ/vLLM | M2 Q4/Ollama | M3 Mistral7B | M4 Coder7B |
|---|---|---|---|---|
| function | 17.5 tok/s | 17.3 tok/s | 44.5 tok/s | 18.5 tok/s |
| class | 16.9 tok/s | 97.2 tok/s | 121.0 tok/s | 56.3 tok/s |
| file | 25.5 tok/s | 153.2 tok/s | 92.6 tok/s | 82.5 tok/s |
| module | 18.3 tok/s | 115.5 tok/s | 65.0 tok/s | 68.4 tok/s |
| complex | 15.9 tok/s | 121.1 tok/s | 65.5 tok/s | 61.7 tok/s |
| **Avg (all tasks)** | **16.0 tok/s** | **117.8 tok/s** | **76.6 tok/s** | **68.9 tok/s** |

> **Finding:** Ollama (llama.cpp backend) is **7× faster** than vLLM for the same 3B model class
> (118 vs 16 tok/s). vLLM is optimized for high-throughput multi-user serving with prefix
> caching — it adds scheduling overhead that dominates for single-user inference. For a local
> coding assistant, Ollama/llama.cpp has far better latency.

### 7.2 Token Consumption Summary

| Model | Total local tok | Total cloud tok | Total time | Cloud cost |
|---|---|---|---|---|
| Claude Sonnet 4.6 | 0 | 3 392 | 40.3s | **$0.0384** |
| M1 AWQ (local_vlm only) | 7 981 | 0 | 373.0s | **$0.0000** |
| M1 AWQ (claude_mcp only) | 18 706 | 36 855 | 1 300.5s | **$0.2008** |
| M2 Q4/Ollama | 21 082 | 0 | 178.9s | **$0.0000** |
| M3 Mistral-7B/Ollama | 29 747 | 0 | 388.5s | **$0.0000** |
| M4 Coder-7B/Ollama | 13 949 | 0 | 202.5s | **$0.0000** |

> **MCP cost note:** `claude_mcp` used $0.20 total for 5 tasks (spec-generation overhead is
> large because Claude writes detailed YAML specs). Cost is mostly fixed at ~$0.04/task
> regardless of local model fix iterations. The savings are real only when `claude_direct`
> would also take many fix iterations — which it doesn't for these tasks (Claude scores 100%).
> MCP makes economic sense for tasks where Claude alone would iterate many times, or when
> used at scale to keep the cloud portion minimal per-task.

### 7.3 Speed vs Quality Comparison

| Model | Overall confidence | Avg tok/s | Total time (5 tasks) | Cost |
|---|---|---|---|---|
| Claude Sonnet 4.6 | **100.0%** | n/a (cloud) | 40s | $0.038 |
| M1 Qwen2.5-3B-AWQ vLLM | **93.3%** | 16 tok/s | 373s | $0.000 |
| M4 Qwen2.5-7B-Q4 Ollama | **86.7%** | 69 tok/s | 203s | $0.000 |
| M2 Qwen2.5-3B-Q4 Ollama | **80.0%** | 118 tok/s | 179s | $0.000 |
| M3 Mistral-7B-Q4 Ollama | **53.3%** | 77 tok/s | 389s | $0.000 |

---

## 8. Claude Direct vs Claude + MCP

Both backends use Claude Sonnet 4.6 for the cloud step. The difference:
- `claude_direct` — Claude generates and fixes all code itself, pay per token on every fix iteration
- `claude_mcp` — Claude writes a YAML spec (one round-trip), then the **local 3B-AWQ model** generates and fixes the code

| Task | Direct time | Direct cost | Direct quality | MCP time | MCP cost | MCP quality | Cost ratio |
|---|---|---|---|---|---|---|---|
| function | 9.9s | $0.00193 | ✓✓✓ TESTS PASS | 61.4s | $0.02601 | ✓✓✓ TESTS PASS | 13.5× |
| class | 4.6s | $0.00320 | ✓✓✓ TESTS PASS | 46.7s | $0.05065 | ✓✓✓ TESTS PASS | 15.8× |
| file | 6.5s | $0.00661 | ✓✓✓ TESTS PASS | 702.2s | $0.03850 | ✓✓✗ **EXEC PASS** | 5.8× |
| module | 12.8s | $0.01652 | ✓✓✓ TESTS PASS | 291.7s | $0.03942 | ✓✓✗ **EXEC PASS** | 2.4× |
| complex | 6.5s | $0.01013 | ✓✓✓ TESTS PASS | 198.5s | $0.04680 | ✓✓✓ TESTS PASS | 4.6× |
| **TOTAL** | **40.3s** | **$0.0384** | **5/5 pass** | **1 300.5s** | **$0.2014** | **3/5 pass** | **5.2×** |

**Result: pure Claude is 5.2× cheaper and 32× faster, with higher quality.**

### Why MCP is more expensive here

The MCP spec-writing step sends Claude a detailed project description to generate a YAML spec.
That spec averages **5 800 input tokens** — compared to the direct prompt average of **208 input
tokens**. The cloud overhead is front-loaded and fixed per task regardless of local model success.

| MCP cost breakdown (avg per task) |
|---|
| Spec-writing cloud tokens: ~5 800 in + 1 300 out → **~$0.035/task** |
| Direct mode tokens: ~228 in + 470 out → **~$0.008/task** |

### When MCP would be cost-effective

MCP wins over direct Claude when the task is hard enough that **Claude itself would need many
fix iterations**. Example: if Claude needed 5 fix iterations on `module` task (adding
~5 000 tokens per retry), direct cost would rise to ~$0.09 — exceeding MCP's fixed ~$0.04.

| Scenario | MCP better? | Why |
|---|---|---|
| Claude solves in 0–2 iterations (as in this benchmark) | No | Direct is cheaper + faster |
| Claude needs 5+ iterations (very complex tasks) | Yes | MCP cost is capped at spec cost |
| Privacy: code must not go to cloud | Yes | Cloud only sees abstract spec |
| Batch of 50+ tasks with same spec | Yes | Spec cost amortized; local model scales |
| Local model quality << Claude quality | No | MCP degrades output (file, module tasks) |

---

## 9. Confidence Analysis

> ⚠️ **Superseded numbers.** The per-model confidence tables in this section were computed under
> the old harness (self-graded tests, stateless loop, boundary strip). The scoring *formula* below
> still applies, but for current figures use [§6.6](#66-canonical-v2-run--model-sweep-2026-07-02--current-figures).

### 8.1 Scoring Formula

Each task is scored across three validation gates:

| Gate | Points | Meaning |
|---|---|---|
| Syntax OK (gate 1) | 1 pt | Code parses without SyntaxError |
| Exec OK (gate 2) | 2 pts | Code runs without runtime error |
| Tests PASS (gate 3) | 3 pts | All pre-written pytest tests pass |

```
Task confidence   = gate_points / 3 × 100 %
Model confidence  = mean(task confidences)
```

### 8.2 Per-Task Confidence per Model

Tasks: function / class / connected / module / project  
(Historical runs used `file` / `complex` — mapped to `connected` / `project` in v2.)

| Task | Sonnet 4.6 | M1-AWQ (v1) | M2-Q4 | M3-Mistral | M4-7B | **M5-Qwen3-4B (v2, rescue)** |
|---|---|---|---|---|---|---|
| function | **100%** | **100%** | **100%** | **100%** | **100%** | **100%** |
| class | **100%** | **100%** | **100%** | 67% | **100%** | 67% |
| connected/file | **100%** | 67% | 67% | 67% | 67% | 67% |
| module | **100%** | **100%** | 67% | 33% | 67% | 67% |
| project/complex | **100%** | **100%** | 67% | 0% | **100%** | **100%** |
| **Overall confidence** | **100.0%** | **93.3%** | **80.0%** | **53.3%** | **86.7%** | **80.0%** |

### 8.3 Summary Table

| Rank | Model | Engine | Overall | function | class | connected | module | project | Context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Claude Sonnet 4.6 | Cloud API | 100% | 100% | 100% | 100% | 100% | 100% | 200K | $0.0384 |
| 2 | Qwen2.5-Coder-3B-AWQ (v1) | vLLM | **93%** | 100% | 100% | 67% | 100% | 100% | 16K | $0.00 |
| 3 | Qwen2.5-Coder-7B-Q4 | Ollama | **87%** | 100% | 100% | 67% | 67% | 100% | 8K | $0.00 |
| 3 | Qwen3-4B-AWQ (rescue) | vLLM | **80%** | 100% | 67% | 67% | 67% | 100% | 6K | $0.014 |
| 3 | Qwen2.5-Coder-3B-Q4 | Ollama | **80%** | 100% | 100% | 67% | 67% | 67% | 16K | $0.00 |
| 4 | Claude Haiku 4.5 (direct) | Cloud API | **80%** | 100% | 33% | 100% | 67% | 100% | 200K | $0.120 |
| 5 | Mistral-7B-Instruct-Q4 | Ollama | **53%** | 100% | 67% | 67% | 33% | 0% | 8K | $0.00 |

---

## 10. Key Findings

### 8.1 Quantization quality matters more than raw parameter count

`Qwen2.5-Coder-3B-AWQ` (M1, 93.3%) outperforms `Qwen2.5-Coder-7B-Q4` (M4, 86.7%)
despite having 4× fewer parameters. AWQ (Activation-aware Weight Quantization) preserves
accuracy far better than GGUF Q4_K_M for code tasks. The 7B model in Q4 loses enough
precision to fail module-level tests that the 3B-AWQ passes.

**Recommendation:** Always prefer AWQ over Q4_K_M for the same model, even if it means
using a smaller parameter count. `Qwen2.5-Coder-3B-AWQ` via vLLM is the best local option
for this GPU.

### 8.2 Code-specialized training beats general instruction tuning

`Mistral-7B-Instruct` (M3, 53.3%) vs `Qwen2.5-Coder-7B` (M4, 86.7%) — same size class,
but Mistral is a general instruction model while Qwen is coding-specialized. The gap widens
dramatically on complex tasks: Mistral completely fails the `complex` task (0%) while
Qwen2.5-Coder-7B passes it (100%).

**Recommendation:** For code generation, always use a coding-specialized model. Mistral-7B
is excellent for chat and reasoning but not competitive with coding models for code synthesis.

### 8.3 The `file` task is the universal bottleneck

All local models fail the `task_queue.py` file-level test (✓✓✗, exec passes, tests fail).
This task requires a `TaskQueue` with correct priority ordering — the models generate code
that runs but has subtle priority comparison bugs that only the pre-written acceptance tests
catch. Claude passes it first try by reasoning about the invariants.

**Implication:** For file-level modules with complex ordering logic, prefer `claude_direct`
or add simpler, more explicit test descriptions.

### 8.4 MCP mode adds latency but maintains quality for 3B models

The `claude_mcp` backend uses ~5× more cloud tokens per call than `claude_direct` for the
spec-writing step, but saves all fix-iteration tokens (which can be 5 000+ for hard tasks).
For simple tasks (function, class, complex), MCP is clearly the right choice — cloud cost
is near-fixed (~$0.03–0.05) regardless of fix iterations.

For the `module` task: `claude_direct` costs $0.017 but `claude_mcp` costs $0.039 and
produced worse output (✓✓✗ vs ✓✓✓). The 3B model struggled to satisfy the more abstractly
written tests that Claude generated.

**Implication:** MCP savings are real, but the quality of Claude-generated tests affects
local model success. Pre-written acceptance tests (as in `exp-bench-local`) avoid this.

### 8.5 Context window is not the bottleneck at these task sizes

All test prompts are under 1 500 tokens even on the longest fix iterations. The 16 384 token
context for 3B models and 8 192 for 7B models is sufficient for all five tasks. The
bottleneck is model capability, not context length.

### 10.6 The harness, not the model, was the bottleneck (methodology fix)

An early v2 run showed `claude_direct` (Haiku 4.5) *failing* the `class` task — SYNTAX OK ✓✗✗
after 5 iterations and $0.06, the costliest run in the benchmark — while a 4B local model "beat"
it. Neither is credible: Haiku can write an LRU cache, and a 4B model does not out-code Haiku.
So we treated the surprise as a harness signal and audited `bench_real.py`. **Three** defects,
each affecting every backend, were corrupting the numbers:

1. **Fenced-block extraction.** Chatty instruct models (Qwen2.5-Coder-3B) return
   "Certainly! Below is…\n```python\n<code>\n```\nThis works by…". The old strip only trimmed
   fences at the *ends*, so the prose stayed and the "code" failed to parse — every chatty-model
   task read as `✗✗✗`. Fixed by extracting the fenced block out of the surrounding prose.
2. **Stateless, truncated fix loop.** Each fix was a fresh one-shot (`code + 800-char error →
   new code`) with no memory. Models oscillated (fix A → break B → revert A), burning tokens
   without converging. Fixed: one **stateful** conversation + **full** error text + **best-of**
   selection (highest gate reached is kept; a late regressing fix can't lower the score).
3. **Self-graded tests.** Each backend generated the pytest it was judged by, so a weak
   test-writer poisoned its own score and the comparison was apples-to-oranges. Fixed: each spec
   ships **canonical `tests:`** and every backend is graded on those identical tests.

The same fix-loop discipline is applied identically to the local loop, `claude_direct`, and the
rescue call. After all three fixes, the picture inverted to something sensible ([§6.6](#66-canonical-v2-run--model-sweep-2026-07-02--current-figures)):
`claude_direct` is a clean 5/5 at $0.022, local models show a real capability gradient, and rescue
reaches 100% at a fraction of cloud cost.

**Implication:** before concluding anything about a *model*, verify the *harness*. Nearly the
entire apparent "capability gap" was fence prose, an oscillating fix loop, and backends grading
themselves.

Context window becomes important for:
- RAG-augmented code generation (injecting codebase context)
- Multi-file generation tasks
- Larger project specs handed to the model in one prompt

---

## 11. Models Not Tested — Blockers and Next Steps

| Model | Blocker | How to resolve |
|---|---|---|
| **Qwen3-4B** | Ollama 0.6.1 too old (requires ≥ 0.7.x) | `! curl -fsSL https://ollama.com/install.sh \| sh` |
| **Ministral-3B** | Not in Ollama registry | Use vLLM: `mistralai/Ministral-3B-Instruct` on HuggingFace (~2 GB) |
| **Ministral-8B** | Not in Ollama registry | Use vLLM: `mistralai/Ministral-8B-Instruct-2410` — tight on 6 GB, try `--max-model-len 4096` |
| **OmniCoder-9B** | No public model by this name found | Possible confusion with DeepSeek-Coder-V2-Lite (16B, too large) or CodeQwen. Suggest `qwen2.5-coder:7b` (tested above) |

---

## 12. Disk and Infrastructure Notes

- Root filesystem (/) was 100% full when attempting 7B model downloads; Ollama model cache
  moved to `/ExternalData/ollama_models` (946 GB free) via `OLLAMA_MODELS` env var.
- vLLM and Ollama cannot share the 6 GB GPU simultaneously. Experiments were run in three
  sequential phases.
- The `dockeduck-base` Docker image (41 GB) on root may be contributing to disk pressure.
  Consider pruning unused images: `docker image prune -a`.

---

## 13. Reproducibility

Benchmark CSVs land in `experiments/results/` (gitignored — regenerated on each run).
The canonical way to reproduce, using the repo-root Makefile (no host pip installs):

```bash
# One-time
cp templates/04-vllm-mcp-coder/.env.example templates/04-vllm-mcp-coder/.env   # add ANTHROPIC_API_KEY for cloud
make -C templates/04-vllm-mcp-coder build
make exp-build

# vLLM backend (template 04)
make start-bench && make wait-ready
make exp-bench-local          # local_vllm only, zero cloud cost      → results/bench_local.csv
make exp-bench-full           # local_vllm + local_vllm_rescue + claude_direct → results/bench_full.csv

# Ollama backend (template 05) — pick model + context in .env, or via exp-try
make start-ollama && make wait-ollama
make exp-bench-ollama         # local_ollama, all tasks               → results/bench_ollama.csv

# One-off manual test of a single model / context window / task:
make exp-try FRAMEWORK=ollama MODEL=qwen2.5-coder:7b CTX=8192 TASK=connected
make exp-try FRAMEWORK=vllm   TASK=project THINKING=true
```

**Full model sweep (§6.6)** — one command runs claude_direct once, then each vLLM model at its
biggest context that fits 6 GB, writing a per-model CSV (`results/bench_<tag>.csv`):

```bash
bash experiments/run_models.sh
```

It rewrites `VLM_MODEL` + `VLM_MAX_MODEL_LEN` in the template `.env`, restarts the bench container,
waits for load (with an OOM-retry at half context), and benchmarks `local_vllm` + `local_vllm_rescue`.
Edit the `run_model` lines to change the model list or context sizes. Context defaults reflect the
GQA KV-cache limits: Coder-1.5B/3B (2 KV heads) → 32K/24K, Qwen3-4B (8 KV heads) → 8K.

To try other models, set `VLM_MODEL` (template 04 `.env`) or `OLLAMA_MODEL` (template 05 `.env`),
e.g. `qwen3:4b`, `mistral:3b`, `qwen2.5-coder:7b`. Cloud model + pricing come from
`CLAUDE_MODEL` / `CLAUDE_INPUT_PRICE` / `CLAUDE_OUTPUT_PRICE` (default Haiku 4.5).

> The `phase*` / `bench_2026*` CSVs still present locally are from the June 2026 historical runs
> tabulated above; the commands here overwrite the canonical `bench_local.csv` / `bench_full.csv` /
> `bench_ollama.csv` with fresh v2 numbers.
