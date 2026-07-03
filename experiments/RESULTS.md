# DockeDuck Benchmark Results — plain-language summary

**One line:** a small local model on a 6 GB laptop GPU, with an occasional Claude "rescue"
call, matches a cloud model's code quality at **~6× lower cost** — and often for **free**.

- **Hardware:** NVIDIA RTX 4050 Laptop, 6 GB VRAM · 32 GB RAM
- **Date:** 2026-07-03 · **Cloud baseline:** Claude Haiku 4.5 ($0.80 / $4.00 per 1M in/out)
- **What "quality" means:** each task passes three gates — the file *parses* (syntax),
  *runs* (exec), then *passes hand-written acceptance tests* (tests). Score per task is
  33 / 67 / 100. "Confidence" is the average across the 5 tasks; "pass" counts tasks that
  clear all three gates.
- **The 5 tasks:** `function` (nested search), `class` (LRU cache), `connected` (chained
  file functions), `module` (config loader), `project` (a small multi-file scaffolder).
- **Fair grading:** every backend — local or cloud — is judged by the *same* canonical
  tests shipped in each task spec, so nobody grades their own homework.

---

## 1. The headline — local + rescue vs. the cloud

*vLLM (AWQ 4-bit) on the 6 GB GPU. "Local only" costs nothing; "+ rescue" calls Claude
only for the tasks the local model fails.*

| Setup | Local only | With rescue | Rescue cost | vs. cloud |
|---|---|---|---|---|
| **Qwen3-4B-AWQ** | 93% · 4/5 · free | **100% · 5/5** | **$0.0036** | **6.1× cheaper** |
| Qwen2.5-Coder-3B-AWQ | 80% · 2/5 · free | 100% · 5/5 | $0.0117 | 1.9× cheaper |
| Qwen2.5-Coder-1.5B-AWQ | 80% · 3/5 · free | 93% · 4/5 | $0.0068 | — |
| Claude Haiku 4.5 (cloud only) | — | 100% · 5/5 | **$0.0221** | baseline |

**How to read it:** Qwen3-4B solves 4 of 5 tasks completely on its own for $0. Claude is
asked to fix only the 1 it misses, so the whole run costs **$0.0036 vs $0.0221** for doing
everything in the cloud — same 100% quality. The weaker the local model, the more rescues
fire and the more you pay, but you never pay *more* than the cloud.

> **Why this works:** the expensive part of cloud coding is the *fix loop* — each retry
> re-sends the whole conversation and gets billed. DockeDuck keeps that loop **local and
> free**, and only pays the cloud for a single rescue when the local model is truly stuck.

---

## 2. Thinking mode — helps local models, wastes money on the cloud

Every reasoning-capable model was run with "thinking" off and on. (Qwen2.5-Coder has no
thinking mode.)

| Model | Thinking OFF | Thinking ON | Verdict |
|---|---|---|---|
| Claude Haiku 4.5 (cloud) | 100% · $0.0221 | 100% · $0.0262 | **no gain, +19% cost** |
| Qwen3-4B-AWQ (vLLM, local) | 93% · 4/5 · free | **100% · 5/5 · free** | **reaches cloud parity, free** |
| qwen3.5:4b (Ollama, + rescue) | 93% · 4/5 · $0.0213 | **100% · 5/5 · $0.0106** | **+1 pass at half the cost** |

**Takeaway:** the cloud model already one-shots these tasks, so making it "think" only burns
tokens. A *local* model, though, genuinely benefits — Qwen3-4B goes from 4/5 to a perfect
5/5 (matching the cloud, for free), and a weaker model both improves and needs cheaper
rescues. **Rule of thumb: thinking OFF for the cloud, ON for capable local models.**

---

## 3. Ollama vs. vLLM — same quality, different trade-offs

The same tasks through Ollama (GGUF Q4):

| Model | Local | + rescue | Note |
|---|---|---|---|
| qwen2.5-coder:1.5b | 67% · 1/5 | 100% · 5/5 · $0.0144 | fast |
| **qwen2.5-coder:3b** | 80% · 2/5 | **100% · 5/5 · $0.0153** | fast, reliable |
| qwen3.5:4b | 67% · 2/5 | 93% · 4/5 · $0.0213 | slow (~59 min); thinking lifts it to 100% |

**Takeaway:** for the coder models, Ollama lands at the *same* pass rate as vLLM — so the
engine is a **latency/ops choice, not a quality one**. vLLM (AWQ) gives you bigger context
windows; Ollama (llama.cpp) is simpler to run and snappier per request. `qwen3.5:4b` is the
exception: slow and weaker unless you turn thinking on.

---

## 4. Context windows — bounded by VRAM and GQA, not by task size

The context window is the largest that fits 6 GB for each model — **the same for thinking
and non-thinking** (it's a memory constraint). What actually limits it is the model's
attention layout (GQA KV-heads), *not* its parameter count:

| Framework | Model | Max context (6 GB) | Why |
|---|---|---|---|
| vLLM | Qwen2.5-Coder-1.5B-AWQ | 32768 | 2 KV heads → small cache/token |
| vLLM | Qwen2.5-Coder-3B-AWQ | 24576 | 2 KV heads |
| vLLM | Qwen3-4B-AWQ | 8192 | **8 KV heads → 4× cache/token** |
| Ollama | qwen2.5-coder:1.5b / :3b | 16384 | — |
| Ollama | qwen3.5:4b | 12288 | thinking needs headroom |

**Surprise:** the *bigger* 4B model has the *smaller* usable context — its 8 KV-heads make
the cache 4× heavier per token, so it OOMs above ~8K while the 3B model comfortably holds
24K. Sizing up the model can mean sizing *down* the context.

---

## 5. What we recommend

- **Best all-round local model (this GPU):** **Qwen3-4B-AWQ** on vLLM — highest quality,
  reaches cloud parity with a single rescue (or with thinking on, for free).
- **Best "just works" Ollama model:** **qwen2.5-coder:3b** — fast, reliable, 100% with rescue.
- **Tiny GPU / CPU:** **qwen2.5-coder:1.5b** — still clears 3/5 tasks unaided.
- **Turn thinking on** for local 4B models; **off** for the cloud.
- **Use the rescue backend** (`local_*_rescue`) in production: it's free when the local
  model succeeds and cheap when it doesn't.

---

## 6. Honest caveats

- These are **5 well-specified tasks** with concrete acceptance criteria — the regime where
  small models shine. Vague specs or large multi-file refactors will favour the cloud model.
- Qwen3-4B **thinking on vLLM is flaky at its 8192 ceiling** on 6 GB (long reasoning traces
  pressure the KV cache → occasional HTTP 400s). Keep `MAX_TOKENS ≤ 4096` there, or use a
  bigger GPU.
- Early versions of this benchmark were **wrong** — a broken fix loop and self-graded tests
  once made Claude look like it "failed" an LRU cache. When a benchmark surprises you,
  suspect the harness first. Full story in
  [`experiments.md` §5](experiments.md#5-the-harness-not-the-model-was-the-bottleneck).

---

*Reproduce everything:* `bash experiments/run_models.sh` (vLLM sweep) and
`bash experiments/run_thinking_ollama.sh` (thinking + Ollama). Full methodology, per-task
tables, and token counts: [`experiments.md`](experiments.md). Architecture:
[`../docs/mcp_technology.md`](../docs/mcp_technology.md).
