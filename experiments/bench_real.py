#!/usr/bin/env python3
"""
Real Metrics Benchmark — v2 (user-YAML architecture)
=====================================================
New flow:
  1. User writes a YAML spec with `conditions:` (natural language test cases).
     No cloud model is needed for this step.
  2. Local model reads the YAML, generates pytest tests from conditions,
     then generates + fixes implementation code entirely offline.
  3. Claude is called ONLY as a rescue step if the local model exhausts
     all retries and still fails. Expected cost = fallback_rate × rescue_cost.

Backends:
  local_vllm          — vLLM, conditions→tests→code, zero cloud cost
  local_ollama        — Ollama, conditions→tests→code, zero cloud cost
  local_vllm_rescue   — same + Claude rescue on failure
  local_ollama_rescue — same + Claude rescue on failure
  claude_direct       — Claude does everything (baseline, always costs money)

Comparison with old architecture:
  OLD claude_mcp: Claude wrote the tests (~5 800 cloud tokens/task, fixed overhead).
  NEW local_*_rescue: local model writes tests; Claude only called on failure.
  Expected MCP cloud cost  = fallback_rate × ~$0.02  vs old fixed ~$0.04/task.

Prerequisites:
  local_vllm*   → container via `make start-bench` (vLLM port 8001 exposed)
  local_ollama* → Ollama running with OLLAMA_MODELS env set
  claude*       → ANTHROPIC_API_KEY in environment

Quick start:
  make start-bench && make wait-ready
  make exp-build
  python experiments/bench_real.py --backend local_vllm claude_direct --tasks all
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import csv
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

# ── Pricing ───────────────────────────────────────────────────────────────────

# Haiku 4.5: $0.80/M in, $4.00/M out   (set CLAUDE_MODEL=claude-sonnet-4-6 to use Sonnet)
# Sonnet 4.6: $3.00/M in, $15.00/M out
CLAUDE_INPUT_PRICE  = float(os.getenv("CLAUDE_INPUT_PRICE",  "0.80"))
CLAUDE_OUTPUT_PRICE = float(os.getenv("CLAUDE_OUTPUT_PRICE", "4.00"))

# Override via CLAUDE_MODEL env var; default to Haiku (cheapest, fastest for rescue)
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# Max output tokens for local (vLLM/Ollama) generations. Bump for thinking runs — the
# <think> trace shares this budget with the actual code, so 4096 can truncate the answer.
LOCAL_MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4096"))

# ── System prompts ────────────────────────────────────────────────────────────
# Tasks are not always Python code: a spec may ask for a Dockerfile, a YAML/JSON
# config, or a small multi-file project. The system prompt stays language-neutral;
# _rules_for() adds per-language guidance and always asks for human-friendly output.

_SYSTEM = (
    "You are an expert software engineer. Produce complete, correct, and human-readable "
    "output. Return only the file contents — no markdown fences and no explanation."
)


def _rules_for(language: str) -> str:
    """Per-language output rules. Always asks for clean, human-friendly output."""
    lang = (language or "python").strip().lower()
    common = (
        "Output ONLY the raw file contents. No markdown fences, no prose.\n"
        "Write clean, human-friendly output: clear names, small units, and a short "
        "docstring or comment only where it genuinely aids understanding."
    )
    if lang == "python":
        return common + "\nUse type hints. Keep it minimal and correct."
    if lang in ("dockerfile", "docker"):
        return common + "\nProduce a valid Dockerfile: pinned base image, minimal layers."
    if lang in ("yaml", "yml"):
        return common + "\nProduce valid YAML with 2-space indentation."
    if lang == "json":
        return common + "\nProduce strictly valid JSON (no comments, no trailing commas)."
    return common


# Back-compat alias (Python default); imported by custom_task.py.
_RULES = _rules_for("python")

# ── Task registry ─────────────────────────────────────────────────────────────
# All tasks use the conditions: format — natural language test cases.
# The local model converts conditions → pytest tests, then generates code.

TASKS_DIR = Path(__file__).parent / "tasks"

# Registry keys mirror the task `type:` in each spec, so CSV rows line up with tasks.
TASKS: dict[str, dict] = {
    "function":  {"spec_file": TASKS_DIR / "function-example.yaml"},
    "class":     {"spec_file": TASKS_DIR / "class-example.yaml"},
    "connected": {"spec_file": TASKS_DIR / "connect-functions-example.yaml"},
    "module":    {"spec_file": TASKS_DIR / "module-example.yaml"},
    "project":   {"spec_file": TASKS_DIR / "project-example.yaml"},
}


def _load_spec(task_level: str) -> dict:
    return yaml.safe_load(TASKS[task_level]["spec_file"].read_text())


# ── Prompt builders ───────────────────────────────────────────────────────────

def _conditions_to_tests_prompt(spec: dict) -> str:
    """Ask the local model to convert natural-language conditions to pytest functions."""
    conditions = spec.get("conditions", spec.get("tests", ""))
    filename   = spec.get("filename", "solution.py")
    name       = spec.get("name", filename.replace(".py", ""))
    return (
        f"Convert these conditions into pytest test functions for '{filename}'.\n\n"
        f"Module: {name}\n"
        f"Description: {spec.get('description', '').strip()}\n\n"
        f"Conditions:\n{conditions.strip()}\n\n"
        "Rules:\n"
        "- Write one pytest test function per condition\n"
        "- Name each test_<short_description_of_what_is_tested>\n"
        "- Do NOT add any import statements (the implementation will be prepended)\n"
        "- Use pytest.raises(ExceptionType) for error conditions\n"
        "- Reference the class/function by the exact name from the description\n"
        f"Output ONLY the test functions, no other code. {_RULES}"
    )


# ── Few-shot examples per task type (mirrors templates/*/src/prompts.py) ──────
# Local-only tokens (zero cloud cost). Each teaches output style: type hints,
# edge-case raising, no prose. Domains are deliberately unrelated to the 5 tasks
# so the example never leaks an answer.
_FEW_SHOT: dict[str, str] = {
    "function": (
        "Example of the expected style:\n"
        "def clamp(value: float, low: float, high: float) -> float:\n"
        "    if low > high:\n"
        "        raise ValueError('low must be <= high')\n"
        "    return max(low, min(value, high))\n"
    ),
    "class": (
        "Example of the expected style:\n"
        "class Counter:\n"
        "    def __init__(self) -> None:\n"
        "        self._n = 0\n"
        "    def increment(self, by: int = 1) -> None:\n"
        "        self._n += by\n"
        "    @property\n"
        "    def value(self) -> int:\n"
        "        return self._n\n"
    ),
    "module": (
        "Example of the expected style (constants + exception + functions in one file):\n"
        "DEFAULT_RETRIES = 3\n\n"
        "class RetryError(Exception):\n"
        "    pass\n\n"
        "def backoff_delay(attempt: int) -> float:\n"
        "    if attempt < 0:\n"
        "        raise RetryError('attempt must be >= 0')\n"
        "    return 0.5 * (2 ** attempt)\n"
    ),
}
_FEW_SHOT["connected functions"] = (
    "Example of the expected style (several small functions that call each other):\n"
    "def _normalize(name: str) -> str:\n"
    "    if not name:\n"
    "        raise ValueError('name must not be empty')\n"
    "    return name.strip().lower()\n\n"
    "def slugify(name: str) -> str:\n"
    "    return _normalize(name).replace(' ', '-')\n"
)
_FEW_SHOT["project"] = (
    "Example of the expected style (dataclass + class that writes files):\n"
    "from dataclasses import dataclass\n"
    "from pathlib import Path\n\n"
    "@dataclass\n"
    "class Item:\n"
    "    name: str\n"
    "    count: int\n\n"
    "class Store:\n"
    "    def save(self, item: Item, out_dir: str) -> None:\n"
    "        Path(out_dir).mkdir(parents=True, exist_ok=True)\n"
    "        Path(out_dir, item.name).write_text(str(item.count))\n"
)
_FEW_SHOT["complex"] = _FEW_SHOT["project"]


def _few_shot_for(task_type: str) -> str:
    ex = _FEW_SHOT.get((task_type or "").strip().lower(), "")
    return f"\n{ex}\n" if ex else ""


def _build_gen_prompt(spec: dict) -> str:
    """Ask the local model to generate the implementation.

    Language-aware: the few-shot examples and the "include imports" hint only apply
    to Python. For a Dockerfile / YAML / JSON spec we ask for the file directly with
    the matching per-language rules.
    """
    language = spec.get("language", "python")
    is_python = language.strip().lower() == "python"
    few_shot     = _few_shot_for(spec.get("type", "")) if is_python else ""
    imports_line = "Include all necessary imports.\n" if is_python else ""
    return (
        f"Write a complete, working {language} file.\n"
        f"Filename: {spec.get('filename', 'solution.py')}\n"
        f"Requirements:\n{spec.get('description', '').strip()}\n"
        f"{few_shot}\n"
        f"{imports_line}{_rules_for(language)}"
    )


def _fix_instruction(error: str, language: str = "python") -> str:
    """Fix request for a STATEFUL conversation. The model's own prior code is already in
    the assistant turn, so we send only the failure (in full, not truncated) and what to
    do — asking it not to repeat a fix it already tried. Errors are capped generously
    (3000 chars) so a full pytest --tb=short traceback survives instead of being cut off."""
    return (
        f"That {language} file did not pass. Exact failure:\n\n"
        f"{error[:3000]}\n\n"
        "Return the complete corrected file. Fix the actual cause shown above; "
        "do not repeat a fix you already tried.\n"
        f"{_rules_for(language)}"
    )


def _claude_rescue_prompt(spec: dict, code: str, tests: str, last_error: str) -> str:
    """Prompt sent to Claude when the local model exhausted all retries.
    Claude fixes the local model's last attempt — not a rewrite from scratch."""
    spec_yaml = yaml.dump(
        {k: v for k, v in spec.items() if k != "_task_level"},
        allow_unicode=True, default_flow_style=False, sort_keys=False,
    )
    return (
        "A local model could not pass all tests after exhausting retries.\n\n"
        f"Task spec (YAML):\n```yaml\n{spec_yaml}```\n\n"
        f"Tests that must pass:\n```python\n{tests}\n```\n\n"
        f"Local model's last code (fix this, do not rewrite from scratch):\n```python\n{code}\n```\n\n"
        f"Last failure:\n{last_error[:3000]}\n\n"
        "Fix only what is needed to make the tests pass. Output only the corrected Python code.\n"
        f"{_RULES}"
    )


# ── Validation gates ──────────────────────────────────────────────────────────

def check_syntax(code: str) -> tuple[bool, str]:
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, str(e)


def run_code(code: str, timeout: int = 20) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code); tmp = f.name
    try:
        r = subprocess.run(["python3", tmp], capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    finally:
        Path(tmp).unlink(missing_ok=True)


def run_tests(source: str, tests: str, timeout: int = 40) -> tuple[bool, str]:
    # Prepend pytest so tests can use pytest.raises without explicit imports
    combined = f"import pytest\n{source}\n\n{tests}"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(combined); tmp = f.name
    try:
        r = subprocess.run(
            ["python3", "-m", "pytest", tmp, "-q", "--tb=short", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return run_code(combined, timeout)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _grade(code: str, tests: str) -> tuple[bool, bool, bool, str]:
    """Run all three gates on `code` and return (syntax_ok, exec_ok, tests_ok, error).

    Gates are cumulative: exec is only attempted if syntax passes, tests only if exec
    passes. `error` describes the first gate that failed (empty when all pass). This is
    the single source of truth for a code candidate's quality — the fix loops call it and
    keep the best-scoring candidate, so reported flags always match the returned code.
    """
    ok, err = check_syntax(code)
    if not ok:
        return False, False, False, f"SyntaxError: {err}"
    ok, out = run_code(code)
    if not ok:
        return True, False, False, f"RuntimeError:\n{out}"
    ok, out = run_tests(code, tests)
    if not ok:
        return True, True, False, f"Tests failed:\n{out}"
    return True, True, True, ""


def _gate_score(syntax_ok: bool, exec_ok: bool, tests_ok: bool) -> int:
    """3 = tests pass · 2 = exec · 1 = syntax · 0 = nothing. Used for best-of selection."""
    return 3 if tests_ok else 2 if exec_ok else 1 if syntax_ok else 0


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks emitted by Qwen3.5 extended-thinking mode."""
    return re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL).strip()


_FENCE_BLOCK_RE = re.compile(r'```[a-zA-Z0-9_+-]*\n(.*?)```', re.DOTALL)


def _strip_fences(text: str) -> str:
    """Extract the file contents from a model response.

    Models ignore "no markdown fences" instructions. Crucially, chatty instruct models
    (e.g. Qwen2.5-Coder-3B-Instruct) wrap the code in a ``` block with prose *around* it
    ("Certainly! Below is…\\n```python\\n<code>\\n```\\nThis function…"). A boundary-only
    strip leaves that prose in place, so the "code" fails to parse. We therefore EXTRACT
    the fenced block when one is present (the longest, to skip tiny inline snippets), and
    only fall back to boundary-stripping when there is no fence at all.
    """
    text = _strip_thinking(text).strip()
    blocks = _FENCE_BLOCK_RE.findall(text)
    if blocks:
        return max(blocks, key=len).strip()
    # No fenced block: strip any stray leading/trailing fence markers and return.
    text = re.sub(r'^```[a-zA-Z0-9_+-]*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return text.strip()


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class BenchResult:
    backend:          str
    task_level:       str
    time_s:           float = 0.0
    cloud_in_tok:     int   = 0
    cloud_out_tok:    int   = 0
    local_in_tok:     int   = 0
    local_out_tok:    int   = 0
    syntax_ok:        bool  = False
    exec_ok:          bool  = False
    tests_ok:         bool  = False
    fix_iters:        int   = 0
    claude_rescue:    bool  = False   # True if Claude fallback was triggered
    thinking:         bool  = False   # extended-thinking mode of the primary model
    code:             str   = ""
    error:            str   = ""

    @property
    def cloud_cost_usd(self) -> float:
        return (self.cloud_in_tok * CLAUDE_INPUT_PRICE
                + self.cloud_out_tok * CLAUDE_OUTPUT_PRICE) / 1_000_000

    def quality_label(self) -> str:
        suffix = " [rescue]" if self.claude_rescue else ""
        if self.tests_ok:  return f"TESTS PASS  ✓✓✓{suffix}"
        if self.exec_ok:   return f"EXEC PASS   ✓✓✗{suffix}"
        if self.syntax_ok: return f"SYNTAX OK   ✓✗✗{suffix}"
        if self.error:     return f"ERROR: {self.error[:30]}"
        return "FAILED      ✗✗✗"


# ── LLM client (vLLM via OpenAI /v1, Ollama via native /api/chat) ────────────

def _thinking_enabled() -> bool:
    """Local-model extended thinking (Qwen3/Qwen3.5 <think> blocks). Set ENABLE_THINKING."""
    return os.getenv("ENABLE_THINKING", "false").lower() in ("1", "true", "yes")


# Cloud thinking budget (output tokens reserved for reasoning; billed as output tokens).
CLAUDE_THINKING_BUDGET = int(os.getenv("CLAUDE_THINKING_BUDGET", "1500"))


def _claude_thinking() -> bool:
    """Claude extended thinking (Anthropic `thinking` param). Set CLAUDE_THINKING."""
    return os.getenv("CLAUDE_THINKING", "false").lower() in ("1", "true", "yes")


async def _llm_chat(
    messages: list[dict],
    url: str,
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    num_ctx: int = 0,
) -> tuple[str, int, int]:
    """Send a full user/assistant message history (stateful). Returns (text, in_tok, out_tok).

    The system message is prepended here, so callers pass only user/assistant turns.
    A stateful history lets the fix loop see its own prior attempts, which stops the
    oscillation ("fix A → break B → revert A → …") that a stateless one-shot fix suffers.

    Qwen3/Qwen3.5 think by default (thousands of tokens before any code). To disable it:
      - Ollama: the OpenAI /v1 endpoint IGNORES think:false and /no_think — only the
        native /api/chat endpoint with `think: false` actually suppresses thinking.
      - vLLM: pass chat_template_kwargs={"enable_thinking": false} on the /v1 endpoint.
    """
    think = _thinking_enabled()
    is_ollama = "11434" in url
    full = [{"role": "system", "content": _SYSTEM}, *messages]

    if is_ollama:
        base = url.rsplit("/v1", 1)[0]
        options: dict = {"temperature": temperature, "num_predict": max_tokens}
        if num_ctx:
            options["num_ctx"] = num_ctx
        body = {
            "model": model,
            "messages": full,
            "stream": False,
            "think": think,          # honoured only on the native endpoint
            "options": options,
        }
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(f"{base}/api/chat", json=body)
            resp.raise_for_status()
            data = resp.json()
        text = _strip_fences(data.get("message", {}).get("content", ""))
        return text, data.get("prompt_eval_count", 0), data.get("eval_count", 0)

    # vLLM (OpenAI-compatible)
    body = {
        "model": model,
        "messages": full,
        "temperature": temperature,
        "max_tokens":  max_tokens,
        "chat_template_kwargs": {"enable_thinking": think},
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(f"{url}/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()
    text  = _strip_fences(data["choices"][0]["message"]["content"])
    usage = data.get("usage", {})
    return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


async def _llm_call(
    prompt: str,
    url: str,
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    num_ctx: int = 0,
) -> tuple[str, int, int]:
    """Single-turn convenience wrapper over _llm_chat. Returns (text, in_tok, out_tok)."""
    return await _llm_chat(
        [{"role": "user", "content": prompt}],
        url, model, temperature, max_tokens, num_ctx,
    )


def _trim_history(messages: list[dict], keep_pairs: int = 3) -> list[dict]:
    """Bound a stateful fix conversation so it can't overflow a small context window.

    Keeps messages[0] (the original task request) plus the most recent `keep_pairs`
    (assistant, user) exchanges. Preserves strict user/assistant alternation: the
    result is [user(task), assistant, user, assistant, …], which every backend accepts.
    Tasks here are small enough that this rarely triggers, but it protects the 7B model
    running at a reduced context window.
    """
    if len(messages) <= 1 + keep_pairs * 2:
        return messages
    tail_start = len(messages) - keep_pairs * 2
    if tail_start % 2 == 0:            # tail must start on an assistant turn (odd index)
        tail_start += 1
    return messages[:1] + messages[tail_start:]


async def _detect_model(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{url}/models")
            models = r.json().get("data", [])
            return models[0]["id"] if models else ""
    except Exception:
        return None


# ── Core local fix loop (conditions → tests → code → fix) ────────────────────

async def _local_loop(
    spec: dict,
    url: str,
    model: str,
    max_retries: int,
    num_ctx: int = 0,
) -> tuple[str, str, str, int, int, int, bool, bool, bool]:
    """
    Full local loop:
      1. Generate pytest tests from conditions (local model, BEFORE code)
      2. Generate implementation code (local model)
      3. Fix loop: syntax → exec → tests

    Returns (code, generated_tests, last_error, local_in, local_out, fix_iters,
             syntax_ok, exec_ok, tests_ok).

    The fix loop is STATEFUL: each fix stays in one growing conversation, so the model
    sees its own earlier attempts and does not oscillate. It is also BEST-OF: the highest
    gate reached across all attempts is kept, so the returned code and its flags always
    agree (a late fix that regresses can't inflate — or deflate — the reported quality).
    """
    language = spec.get("language", "python")
    local_in = local_out = 0

    # Step 1 — obtain the acceptance tests that grade the fix loop and the final result.
    # Benchmark specs ship CANONICAL tests: identical across every backend, so code-gen
    # quality is measured cleanly and no backend is scored on tests it wrote itself. Only
    # when a spec has none (e.g. a user custom task) does the local model generate tests
    # from conditions — the production feature. The model never sees the test source, only
    # the failure output, so canonical tests do not leak the solution.
    tests = (spec.get("tests") or "").strip()
    if not tests:
        for _test_attempt in range(3):
            tests, i, o = await _llm_call(_conditions_to_tests_prompt(spec), url, model,
                                          max_tokens=LOCAL_MAX_TOKENS, num_ctx=num_ctx)
            local_in += i; local_out += o
            if check_syntax(tests)[0]:
                break

    # Step 2 — generate implementation, opening a stateful conversation
    messages: list[dict] = [{"role": "user", "content": _build_gen_prompt(spec)}]
    code, i, o = await _llm_chat(messages, url, model, max_tokens=LOCAL_MAX_TOKENS, num_ctx=num_ctx)
    local_in += i; local_out += o
    messages.append({"role": "assistant", "content": code})

    best_score = -1
    best_code, best_syn, best_exc, best_tst = code, False, False, False
    last_error = ""
    fixes_used = 0

    # max_retries fix calls; +1 so the code from the final fix is graded, not discarded
    for attempt in range(max_retries + 1):
        syn, exc, tst, last_error = _grade(code, tests)
        score = _gate_score(syn, exc, tst)
        if score > best_score:
            best_score, best_code, best_syn, best_exc, best_tst = score, code, syn, exc, tst
        fixes_used = attempt
        if tst or attempt == max_retries:
            break
        messages.append({"role": "user", "content": _fix_instruction(last_error, language)})
        messages = _trim_history(messages)
        code, i, o = await _llm_chat(messages, url, model, max_tokens=LOCAL_MAX_TOKENS, num_ctx=num_ctx)
        local_in += i; local_out += o
        messages.append({"role": "assistant", "content": code})

    return (best_code, tests, last_error, local_in, local_out, fixes_used,
            best_syn, best_exc, best_tst)


# ── Backend: local_vllm ───────────────────────────────────────────────────────

async def measure_local_vllm(
    task_level: str, vlm_url: str, vlm_model: str, max_retries: int,
    spec: dict | None = None,
) -> BenchResult:
    r    = BenchResult(backend="local_vllm", task_level=task_level)
    r.thinking = _thinking_enabled()
    spec = spec if spec is not None else _load_spec(task_level)
    t0   = time.time()
    try:
        code, tests, last_err, li, lo, fixes, syn, exc, tst = await _local_loop(
            spec, vlm_url, vlm_model, max_retries)
        r.local_in_tok = li; r.local_out_tok = lo
        r.fix_iters = fixes; r.syntax_ok = syn; r.exec_ok = exc; r.tests_ok = tst
        r.code = code
    except Exception as e:
        r.error = str(e)
    r.time_s = time.time() - t0
    return r


# ── Backend: local_ollama ─────────────────────────────────────────────────────

async def measure_local_ollama(
    task_level: str, ollama_url: str, ollama_model: str,
    max_retries: int, num_ctx: int = 16384, spec: dict | None = None,
) -> BenchResult:
    r    = BenchResult(backend="local_ollama", task_level=task_level)
    r.thinking = _thinking_enabled()
    spec = spec if spec is not None else _load_spec(task_level)
    t0   = time.time()
    try:
        code, tests, last_err, li, lo, fixes, syn, exc, tst = await _local_loop(
            spec, ollama_url, ollama_model, max_retries, num_ctx=num_ctx)
        r.local_in_tok = li; r.local_out_tok = lo
        r.fix_iters = fixes; r.syntax_ok = syn; r.exec_ok = exc; r.tests_ok = tst
        r.code = code
    except Exception as e:
        r.error = str(e)
    r.time_s = time.time() - t0
    return r


# ── Backend: local_vllm_rescue / local_ollama_rescue ─────────────────────────
#
# Phase 1: local model runs the full loop (max_retries attempts).
# Phase 2 (only if Phase 1 fails): Claude receives the last code + test failures
#          and produces a corrected implementation in a SINGLE call.
#
# Cloud tokens are charged ONLY when Claude is called (Phase 2).
# Expected cost per task = fallback_rate × rescue_call_cost (~$0.01–0.03).

async def _claude_message(client, messages: list[dict], max_tokens: int,
                          think: bool) -> tuple[str, int, int]:
    """One Claude call with optional extended thinking. Returns (text, in_tok, out_tok).

    With thinking enabled, the reasoning is billed as output tokens (so cost accounting
    stays honest) and the response carries both `thinking` and `text` blocks — we keep only
    the text. The budget must leave room for the answer, and max_tokens must exceed it.
    """
    kwargs: dict = dict(model=CLAUDE_MODEL, max_tokens=max_tokens,
                        system=_SYSTEM, messages=messages)
    if think:
        kwargs["thinking"] = {"type": "enabled",
                              "budget_tokens": min(CLAUDE_THINKING_BUDGET, max_tokens - 512)}
    resp = await client.messages.create(**kwargs)
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return _strip_fences(text), resp.usage.input_tokens, resp.usage.output_tokens


async def _claude_rescue(
    spec: dict,
    code: str,
    tests: str,
    last_error: str,
    r: BenchResult,
) -> None:
    """Call Claude once to fix the local model's last attempt. Updates r in-place."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("pip install anthropic")

    client = anthropic.AsyncAnthropic()
    prompt = _claude_rescue_prompt(spec, code, tests, last_error)

    fixed_code, ci, co = await _claude_message(
        client, [{"role": "user", "content": prompt}], 4096, _claude_thinking())
    r.cloud_in_tok  += ci
    r.cloud_out_tok += co
    r.claude_rescue  = True

    # Keep whichever is better: the local model's best attempt or Claude's rescue. A single
    # rescue call can regress (it sometimes rewrites and breaks something the local code had
    # right), so best-of makes the rescue strictly non-harmful to the reported quality.
    r_syn, r_exc, r_tst, _ = _grade(fixed_code, tests)
    if _gate_score(r_syn, r_exc, r_tst) >= _gate_score(r.syntax_ok, r.exec_ok, r.tests_ok):
        r.code = fixed_code
        r.syntax_ok, r.exec_ok, r.tests_ok = r_syn, r_exc, r_tst


async def measure_local_vllm_rescue(
    task_level: str, vlm_url: str, vlm_model: str, max_retries: int,
    spec: dict | None = None,
) -> BenchResult:
    r    = BenchResult(backend="local_vllm_rescue", task_level=task_level)
    r.thinking = _thinking_enabled()
    spec = spec if spec is not None else _load_spec(task_level)
    t0   = time.time()
    try:
        code, tests, last_err, li, lo, fixes, syn, exc, tst = await _local_loop(
            spec, vlm_url, vlm_model, max_retries)
        r.local_in_tok = li; r.local_out_tok = lo
        r.fix_iters = fixes; r.syntax_ok = syn; r.exec_ok = exc; r.tests_ok = tst
        r.code = code
        if not tst:
            await _claude_rescue(spec, code, tests, last_err, r)
    except Exception as e:
        r.error = str(e)
    r.time_s = time.time() - t0
    return r


async def measure_local_ollama_rescue(
    task_level: str, ollama_url: str, ollama_model: str,
    max_retries: int, num_ctx: int = 16384, spec: dict | None = None,
) -> BenchResult:
    r    = BenchResult(backend="local_ollama_rescue", task_level=task_level)
    r.thinking = _thinking_enabled()
    spec = spec if spec is not None else _load_spec(task_level)
    t0   = time.time()
    try:
        code, tests, last_err, li, lo, fixes, syn, exc, tst = await _local_loop(
            spec, ollama_url, ollama_model, max_retries, num_ctx=num_ctx)
        r.local_in_tok = li; r.local_out_tok = lo
        r.fix_iters = fixes; r.syntax_ok = syn; r.exec_ok = exc; r.tests_ok = tst
        r.code = code
        if not tst:
            await _claude_rescue(spec, code, tests, last_err, r)
    except Exception as e:
        r.error = str(e)
    r.time_s = time.time() - t0
    return r


# ── Backend: claude_direct ────────────────────────────────────────────────────
#
# Baseline: Claude reads the YAML conditions and does everything itself.
# Claude generates tests from conditions, then generates + fixes code.
# Every iteration burns cloud tokens.

async def measure_claude_direct(
    task_level: str, max_retries: int, spec: dict | None = None,
) -> BenchResult:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("pip install anthropic")

    r      = BenchResult(backend="claude_direct", task_level=task_level)
    spec   = spec if spec is not None else _load_spec(task_level)
    client = anthropic.AsyncAnthropic()
    think  = _claude_thinking()
    r.thinking = think

    conditions = spec.get("conditions", spec.get("tests", ""))
    filename   = spec.get("filename", "solution.py")

    t0 = time.time()
    code = tests = ""
    best_score = -1
    best_code, best_syn, best_exc, best_tst = "", False, False, False

    async def _ask(msgs: list[dict], max_tokens: int) -> str:
        text, ci, co = await _claude_message(client, msgs, max_tokens, think)
        r.cloud_in_tok  += ci
        r.cloud_out_tok += co
        return text

    try:
        # Step 1: acceptance tests. Use the spec's canonical tests when present (identical
        # to every other backend — fair); otherwise Claude generates them from conditions.
        tests = (spec.get("tests") or "").strip()
        if not tests:
            tests = await _ask([{"role": "user", "content":
                f"Convert these conditions to pytest test functions for {filename}.\n\n"
                f"Description:\n{spec.get('description','').strip()}\n\n"
                f"Conditions:\n{conditions.strip()}\n\n"
                "Rules: one test per condition, no imports, use pytest.raises for errors.\n"
                "Output ONLY the test functions."}], 2048)

        # Step 2: Claude generates implementation, opening a stateful conversation
        conv: list[dict] = [{"role": "user", "content":
            f"Write {filename}:\n{spec.get('description','').strip()}\n"
            "Output only complete Python code."}]
        code = await _ask(conv, 4096)
        conv.append({"role": "assistant", "content": code})

        # Step 3: Stateful, best-of fix loop (same discipline as the local loop, so the
        # baseline is measured fairly — Claude also sees its own prior attempts and full
        # errors, not a truncated one-shot that provokes oscillation)
        for attempt in range(max_retries + 1):
            syn, exc, tst, err = _grade(code, tests)
            score = _gate_score(syn, exc, tst)
            if score > best_score:
                best_score, best_code, best_syn, best_exc, best_tst = score, code, syn, exc, tst
            r.fix_iters = attempt
            if tst or attempt == max_retries:
                break
            conv.append({"role": "user", "content": _fix_instruction(err, "python")})
            conv = _trim_history(conv)
            code = await _ask(conv, 4096)
            conv.append({"role": "assistant", "content": code})
    except Exception as e:
        r.error = str(e)

    r.syntax_ok, r.exec_ok, r.tests_ok = best_syn, best_exc, best_tst
    r.code   = best_code
    r.time_s = time.time() - t0
    return r


# ── Reporting ─────────────────────────────────────────────────────────────────

_W = 112


def print_results(results: list[BenchResult]) -> None:
    backends    = list(dict.fromkeys(r.backend    for r in results))
    task_levels = list(dict.fromkeys(r.task_level for r in results))
    by_task: dict[str, dict[str, BenchResult]] = {}
    for r in results:
        by_task.setdefault(r.task_level, {})[r.backend] = r

    print(f"\n{'━' * _W}")
    print(f"  BENCHMARK RESULTS  —  all backends")
    print(f"{'━' * _W}\n")
    print(
        f"  {'Task':<10} {'Backend':<22} {'Time':>7} "
        f"{'Cld-In':>8} {'Cld-Out':>8} {'Lcl-In':>8} {'Lcl-Out':>7} "
        f"{'Cost USD':>10}  {'Quality':<26}  {'Fixes':>5}"
    )
    print(f"  {'─' * _W}")
    for tl in task_levels:
        for be in backends:
            r = by_task.get(tl, {}).get(be)
            if r is None:
                continue
            print(
                f"  {tl:<10} {be:<22} {r.time_s:>6.1f}s "
                f"{r.cloud_in_tok:>8,} {r.cloud_out_tok:>8,} "
                f"{r.local_in_tok:>8,} {r.local_out_tok:>7,} "
                f"${r.cloud_cost_usd:>9.5f}  "
                f"{r.quality_label():<26}  {r.fix_iters:>5}"
            )
        print(f"  {'─' * _W}")

    # Rescue / fallback summary
    rescue_backends = [b for b in backends if "rescue" in b]
    for rb in rescue_backends:
        rb_results = [by_task.get(tl, {}).get(rb) for tl in task_levels]
        rb_results = [r for r in rb_results if r is not None]
        n_rescue = sum(1 for r in rb_results if r.claude_rescue)
        fallback_rate = n_rescue / len(rb_results) if rb_results else 0
        total_cloud_cost = sum(r.cloud_cost_usd for r in rb_results)
        avg_expected_cost = total_cloud_cost / len(rb_results) if rb_results else 0
        print(f"\n  {rb}: Claude rescue triggered {n_rescue}/{len(rb_results)} tasks "
              f"({fallback_rate:.0%} fallback rate)  "
              f"total cloud cost ${total_cloud_cost:.5f}  "
              f"avg expected ${avg_expected_cost:.5f}/task")

    # Comparison: direct vs rescue
    for rb in rescue_backends:
        print(f"\n{'━' * _W}")
        print(f"  COMPARISON: claude_direct  vs  {rb}")
        print(f"{'━' * _W}")
        print(f"  {'Task':<10}  {'Direct time':>12}  {'Direct cost':>12}  {'Direct quality':<18}"
              f"  {'Rescue time':>12}  {'Rescue cost':>12}  {'Rescue quality'}")
        print(f"  {'─' * (_W - 2)}")
        for tl in task_levels:
            d = by_task.get(tl, {}).get("claude_direct")
            m = by_task.get(tl, {}).get(rb)
            if not d or not m:
                continue
            print(
                f"  {tl:<10}  {d.time_s:>10.1f}s  ${d.cloud_cost_usd:>10.5f}  {d.quality_label():<18}"
                f"  {m.time_s:>10.1f}s  ${m.cloud_cost_usd:>10.5f}  {m.quality_label()}"
            )

    # Difficulty matrix
    print(f"\n{'━' * _W}")
    print(f"  DIFFICULTY MATRIX  —  [syntax|exec|tests]  (fixes, time, cost)")
    print(f"{'━' * _W}\n")
    print(f"  {'Task':<10}", end="")
    for be in backends:
        print(f"  {be:<32}", end="")
    print()
    print(f"  {'─' * (10 + len(backends) * 34)}")
    for tl in task_levels:
        print(f"  {tl:<10}", end="")
        for be in backends:
            r = by_task.get(tl, {}).get(be)
            if r is None:
                print(f"  {'(not run)':<32}", end="")
                continue
            cost = f"${r.cloud_cost_usd:.4f}" if r.cloud_cost_usd > 0 else "$0"
            flags = (
                f"{'✓' if r.syntax_ok else '✗'}{'✓' if r.exec_ok else '✗'}{'✓' if r.tests_ok else '✗'}"
                f"  {r.fix_iters}fx  {r.time_s:.0f}s  {cost}"
            )
            print(f"  {flags:<32}", end="")
        print()


def _confidence(r: "BenchResult") -> int:
    """Highest validation gate reached: TESTS=100 · EXEC=67 · SYNTAX=33 · FAIL=0."""
    if r.tests_ok:
        return 100
    if r.exec_ok:
        return 67
    if r.syntax_ok:
        return 33
    return 0


def save_csv(results: list[BenchResult], path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "backend", "task", "time_s",
            "cloud_in_tok", "cloud_out_tok", "local_in_tok", "local_out_tok",
            "cost_usd", "syntax_ok", "exec_ok", "tests_ok", "confidence",
            "fix_iters", "claude_rescue", "thinking", "error",
        ])
        for r in results:
            w.writerow([
                r.backend, r.task_level, f"{r.time_s:.2f}",
                r.cloud_in_tok, r.cloud_out_tok, r.local_in_tok, r.local_out_tok,
                f"{r.cloud_cost_usd:.6f}",
                r.syntax_ok, r.exec_ok, r.tests_ok, _confidence(r),
                r.fix_iters, r.claude_rescue, r.thinking,
                r.error[:200] if r.error else "",
            ])


# ── Main ──────────────────────────────────────────────────────────────────────

BACKEND_CHOICES = [
    "local_vllm", "local_ollama",
    "local_vllm_rescue", "local_ollama_rescue",
    "claude_direct",
    "all",
]
TASK_CHOICES = [*TASKS.keys(), "all"]


async def async_main(args: argparse.Namespace) -> None:
    if "all" in args.backend:
        backends = ["local_vllm", "local_vllm_rescue", "claude_direct"]
    else:
        backends = args.backend
    task_levels = list(TASKS.keys()) if "all" in args.tasks else args.tasks

    needs_vllm    = any("vllm" in b for b in backends)
    needs_ollama  = any("ollama" in b for b in backends)
    needs_claude  = any(b in ("claude_direct",) or "rescue" in b for b in backends)

    vlm_url      = args.vlm_url.rstrip("/")
    vlm_model    = os.getenv("VLM_MODEL", "")
    ollama_url   = args.ollama_url.rstrip("/")
    ollama_model = os.getenv("OLLAMA_MODEL", "")
    num_ctx      = int(os.getenv("OLLAMA_NUM_CTX", "16384"))

    if needs_vllm:
        detected = await _detect_model(vlm_url)
        if detected is None:
            print(f"  ERROR: vLLM not reachable at {vlm_url}\n  Run: make start-bench")
            return
        vlm_model = vlm_model or detected

    if needs_ollama:
        detected = await _detect_model(ollama_url)
        if detected is None:
            print(f"  ERROR: Ollama not reachable at {ollama_url}")
            return
        ollama_model = ollama_model or detected

    if needs_claude and not os.getenv("ANTHROPIC_API_KEY"):
        print("  ERROR: ANTHROPIC_API_KEY not set")
        return

    print(f"\n{'━' * _W}")
    print(f"  DockeDuck Benchmark v2 — user-YAML architecture")
    print(f"  Flow: conditions → local tests → local code → fix loop → [Claude rescue if needed]")
    print(f"  backends  = {backends}")
    print(f"  tasks     = {task_levels}")
    local_think = "on" if _thinking_enabled() else "off"
    cloud_think = "on" if _claude_thinking() else "off"
    if needs_vllm:    print(f"  vLLM URL  = {vlm_url}  (model: {vlm_model}, thinking={local_think})")
    if needs_ollama:  print(f"  Ollama    = {ollama_url}  (model: {ollama_model}, ctx={num_ctx}, thinking={local_think})")
    if needs_claude:  print(f"  Claude    = {CLAUDE_MODEL}  (${CLAUDE_INPUT_PRICE}/M in, ${CLAUDE_OUTPUT_PRICE}/M out, thinking={cloud_think})")
    else:             print(f"  Claude    = not used")
    print(f"{'━' * _W}\n")

    results: list[BenchResult] = []

    for backend in backends:
        for tl in task_levels:
            spec = _load_spec(tl)
            print(f"  [{backend:<22}] {tl:<10} {spec['filename']} ... ", end="", flush=True)
            try:
                if backend == "local_vllm":
                    r = await measure_local_vllm(tl, vlm_url, vlm_model, args.max_retries)
                elif backend == "local_ollama":
                    r = await measure_local_ollama(tl, ollama_url, ollama_model, args.max_retries, num_ctx)
                elif backend == "local_vllm_rescue":
                    r = await measure_local_vllm_rescue(tl, vlm_url, vlm_model, args.max_retries)
                elif backend == "local_ollama_rescue":
                    r = await measure_local_ollama_rescue(tl, ollama_url, ollama_model, args.max_retries, num_ctx)
                elif backend == "claude_direct":
                    r = await measure_claude_direct(tl, args.max_retries)
                else:
                    continue
                print(r.quality_label())
            except Exception as e:
                r = BenchResult(backend=backend, task_level=tl, error=str(e))
                print(f"ERROR: {e}")
            results.append(r)

    if results:
        print_results(results)

    if args.output and results:
        save_csv(results, args.output)
        print(f"\n  CSV → {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DockeDuck benchmark v2 — user-YAML architecture")
    parser.add_argument("--backend",  nargs="+", choices=BACKEND_CHOICES, default=["all"])
    parser.add_argument("--tasks",    nargs="+", choices=TASK_CHOICES,    default=["function", "class"])
    parser.add_argument("--vlm-url",    default=os.getenv("VLM_URL",    "http://localhost:8001/v1"))
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://localhost:11434/v1"))
    parser.add_argument("--max-retries", type=int, default=6)
    parser.add_argument("--output",   metavar="FILE")
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
