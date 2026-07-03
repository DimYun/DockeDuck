#!/usr/bin/env python3
"""
MCP Coder Experiments
=====================
Measures three axes for the DockeDuck MCP-coder idea:
  1. TOKEN SAVINGS    — cloud tokens with/without MCP offload, per task size
  2. CONTEXT GROWTH   — context window pressure over a multi-step coding session
  3. TEST STRATEGIES  — who generates border-case tests (local VLM vs cloud LLM)
  4. USAGE PATTERNS   — IDE (targeted calls) vs Claude autonomous (write_and_fix)

Architecture recap:
  Cloud LLM (Claude) → short tool call → MCP server → local VLM generates code
  Cloud LLM sees only: task description + SYNTAX_OK status header
  Local VLM sees:      full code prompt + generates full code (zero API cost)

Modes:
  --mode dry   Estimate tokens from prompt templates (no API calls needed)
  --mode live  Call local VLM + Anthropic API for real token counts
               Requires: running vLLM server (VLLM_PORT, VLM_MODEL) + ANTHROPIC_API_KEY

Run examples:
  python experiments/mcp_experiments.py
  python experiments/mcp_experiments.py --mode dry --exp context
  python experiments/mcp_experiments.py --mode live --exp live_tokens --task class
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path

# ── Token estimation (dry mode) ───────────────────────────────────────────────

def est_tokens(text: str) -> int:
    """~4 chars per token — standard GPT/Claude family approximation."""
    return max(1, len(text) // 4)


def count_tokens_anthropic(messages: list[dict], model: str = "claude-sonnet-4-6") -> int:
    """Count tokens via Anthropic SDK; falls back to estimate if unavailable."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.beta.messages.count_tokens(model=model, messages=messages)
        return resp.input_tokens
    except Exception:
        return sum(est_tokens(str(m.get("content", ""))) for m in messages)


# ── Sample tasks — 4 complexity levels ───────────────────────────────────────

TASKS: dict[str, dict] = {
    "function": {
        "description": (
            "Binary search on a sorted list of integers. "
            "Return the index of target. Raise ValueError if not found."
        ),
        "filename": "binary_search.py",
        "signature": "def binary_search(sorted_list: list[int], target: int) -> int",
        # realistic generated code size in chars
        "code_chars": 550,
    },
    "class": {
        "description": "LRU Cache with get/put methods. O(1) time complexity using OrderedDict.",
        "filename": "lru_cache.py",
        "name": "LRUCache",
        "methods": (
            "__init__(self, capacity: int)\n"
            "get(self, key: int) -> int  # return -1 if not found\n"
            "put(self, key: int, value: int) -> None"
        ),
        "code_chars": 1_800,
    },
    "file": {
        "description": (
            "A Python module implementing a priority task queue. "
            "Tasks have: id, name, priority (1-10), payload dict. "
            "Queue supports: enqueue, dequeue, peek, size, clear, list_by_priority."
        ),
        "filename": "task_queue.py",
        "code_chars": 4_800,
    },
    "module": {
        "description": (
            "A Python module for loading and validating config files (TOML/JSON/YAML). "
            "Classes: ConfigLoader (loads from file/env), ConfigSchema (validates keys+types), "
            "ConfigError (custom exception). "
            "Functions: load_config(path), validate_config(data, schema), merge_configs(*configs). "
            "Full error handling, type hints."
        ),
        "filename": "config_loader.py",
        "code_chars": 11_000,
    },
}

# ── Prompt templates (mirrors src/prompts.py) ─────────────────────────────────

_RULES = (
    "Output ONLY raw code. No markdown fences, no prose, no explanation.\n"
    "Use type hints. Keep it minimal and correct."
)


def file_prompt(description: str, filename: str, context: str = "") -> str:
    ctx = f"\n\nContext:\n{context}" if context else ""
    return (
        f"Write a complete, working Python file.\n"
        f"Filename: {filename}\n"
        f"Requirements: {description}"
        f"{ctx}\n\nInclude all necessary imports.\n{_RULES}"
    )


def function_prompt(signature: str, description: str, context: str = "") -> str:
    ctx = f"\n\nContext:\n{context}" if context else ""
    return (
        f"Implement the following Python function.\n"
        f"Signature: {signature}\n"
        f"Description: {description}"
        f"{ctx}\n\nHandle edge cases.\n{_RULES}"
    )


def class_prompt(name: str, description: str, methods: str, context: str = "") -> str:
    ctx = f"\n\nContext:\n{context}" if context else ""
    return (
        f"Implement the following Python class.\n"
        f"Class name: {name}\n"
        f"Description: {description}\n"
        f"Methods:\n{methods}"
        f"{ctx}\n\n{_RULES}"
    )


def test_prompt(code: str) -> str:
    return (
        "Write pytest border-case tests for the code below.\n\n"
        f"{code}\n\n"
        "Rules:\n"
        "- Output ONLY pytest test functions named test_*.\n"
        "- Test edge cases: empty inputs, boundary values, None, type errors.\n"
        "- Do NOT import the module under test — its code is already in scope.\n"
        f"{_RULES}"
    )


def fix_prompt(code: str, error: str) -> str:
    return (
        f"The following Python code has an error. Fix it.\n\n"
        f"Code:\n{code}\n\nError:\n{error}\n\n{_RULES}"
    )


# ── Experiment 1: Token savings ───────────────────────────────────────────────

@dataclass
class TokenComparison:
    task_level: str

    # ── Direct path: Claude generates code inline ──────────────────────────
    direct_cloud_in: int       # task description sent to Claude
    direct_cloud_out: int      # code Claude must generate (= output tokens!)

    # ── MCP path: Claude orchestrates, local VLM generates ─────────────────
    mcp_cloud_in: int          # task + tool schema overhead → Claude input
    mcp_cloud_out: int         # tool call JSON → Claude output (tiny!)
    mcp_local_in: int          # detailed prompt → local VLM input (free)
    mcp_local_out: int         # generated code → local VLM output (free)
    mcp_cloud_result: int      # status header fed back to Claude

    # ── write_and_fix loop (N offline iterations) ──────────────────────────
    fix_iterations: int = 3

    # Derived: single-shot totals
    @property
    def direct_total(self) -> int:
        return self.direct_cloud_in + self.direct_cloud_out

    @property
    def mcp_cloud_total(self) -> int:
        return self.mcp_cloud_in + self.mcp_cloud_out + self.mcp_cloud_result

    @property
    def cloud_savings(self) -> int:
        return self.direct_total - self.mcp_cloud_total

    @property
    def cloud_savings_pct(self) -> float:
        return self.cloud_savings / self.direct_total * 100 if self.direct_total else 0.0

    # Derived: fix-loop totals
    @property
    def direct_fixloop(self) -> int:
        # Each iteration: Claude sees full code (in) + re-generates it (out)
        per_iter_in = self.direct_cloud_out + 150   # code + error msg
        per_iter_out = self.direct_cloud_out
        return self.direct_cloud_in + (per_iter_in + per_iter_out) * self.fix_iterations

    @property
    def mcp_fixloop(self) -> int:
        # One tool call → write_and_fix loops entirely on local VLM
        done_result = 30   # "# DONE after 3 attempt(s)\n# file: foo.py"
        return self.mcp_cloud_in + self.mcp_cloud_out + done_result

    @property
    def fixloop_savings(self) -> int:
        return self.direct_fixloop - self.mcp_fixloop

    @property
    def fixloop_savings_pct(self) -> float:
        return self.fixloop_savings / self.direct_fixloop * 100 if self.direct_fixloop else 0.0


# Approximate tool schema overhead (one tool definition in the API request)
_TOOL_SCHEMA_TOKENS = est_tokens(
    '{"name":"write_code_file","description":"Generate a complete code file",'
    '"input_schema":{"type":"object","properties":{'
    '"description":{"type":"string"},"filename":{"type":"string"},'
    '"language":{"type":"string"},"context":{"type":"string"}},'
    '"required":["description","filename"]}}'
)


def analyze_token_savings() -> list[TokenComparison]:
    results: list[TokenComparison] = []

    for level, task in TASKS.items():
        desc = task["description"]
        fname = task["filename"]
        code_tokens = task["code_chars"] // 4

        # Build the local VLM prompt to get its real token size
        if level == "function":
            local_prompt = function_prompt(task["signature"], desc)
        elif level == "class":
            local_prompt = class_prompt(task["name"], desc, task["methods"])
        else:
            local_prompt = file_prompt(desc, fname)

        # Direct path: Claude gets a short user request, must output full code
        direct_in = est_tokens(f"Write {fname}:\n{desc}")
        direct_out = code_tokens

        # MCP path
        mcp_cloud_in = direct_in + _TOOL_SCHEMA_TOKENS
        mcp_cloud_out = est_tokens(
            f'{{"description":"{desc[:40]}...","filename":"{fname}"}}'
        )
        mcp_local_in = est_tokens(local_prompt)
        mcp_local_out = code_tokens
        # Cloud only sees status header when trusting SYNTAX_OK
        mcp_result = est_tokens(f"# SYNTAX_OK\n# file: {fname}")

        results.append(TokenComparison(
            task_level=level,
            direct_cloud_in=direct_in,
            direct_cloud_out=direct_out,
            mcp_cloud_in=mcp_cloud_in,
            mcp_cloud_out=mcp_cloud_out,
            mcp_local_in=mcp_local_in,
            mcp_local_out=mcp_local_out,
            mcp_cloud_result=mcp_result,
        ))

    return results


# ── Experiment 2: Context window growth over a coding session ─────────────────

@dataclass
class SessionStep:
    step: int
    action: str
    code_chars: int

    direct_ctx: int    # cumulative Claude context tokens (no MCP)
    mcp_ctx: int       # cumulative Claude context tokens (with MCP)
    local_ctx: int     # local VLM context (stateless per call — reset each time)


# Claude Sonnet 4.6 context limit
CONTEXT_LIMIT = 200_000


def simulate_context_growth() -> list[SessionStep]:
    """Simulate building a small web-scraper module in 5 steps."""
    session = [
        # (human-readable label, generated code chars)
        ("write_function: fetch_url(url)",              400),
        ("write_function: parse_html(html, selector)",  600),
        ("write_class: ScraperCache (get/set/ttl)",   1_200),
        ("write_code_file: scraper.py (full module)",  3_200),
        ("validate_and_test: border-case pytest suite",  800),
    ]

    steps: list[SessionStep] = []
    # Initial system prompt
    direct_ctx = est_tokens(
        "You are an expert software engineer. Help the user build a Python web scraper."
    )
    mcp_ctx = direct_ctx

    for i, (label, code_chars) in enumerate(session, 1):
        task_tokens = est_tokens(label)
        code_tokens = code_chars // 4

        # Direct: user message (task) + assistant message (full code) accumulate
        direct_ctx += task_tokens + code_tokens

        # MCP: user message (task) + assistant message (tool call JSON ~30) +
        #      tool result (status header ~15) accumulate — code never enters Claude's ctx
        mcp_ctx += task_tokens + 30 + 15

        # Local VLM: stateless — only sees the current prompt
        local_ctx = task_tokens + 80 + code_tokens   # system hint + task + code

        steps.append(SessionStep(
            step=i,
            action=label,
            code_chars=code_chars,
            direct_ctx=direct_ctx,
            mcp_ctx=mcp_ctx,
            local_ctx=local_ctx,
        ))

    return steps


# ── Experiment 3: Who generates border-case tests? ───────────────────────────

@dataclass
class TestStrategy:
    name: str
    cloud_tokens: int          # tokens charged to cloud API for test generation
    local_tokens: int          # tokens processed by local VLM
    code_in_cloud_ctx: bool    # does the source code enter Claude's context?
    coverage_note: str
    latency_note: str
    cost_note: str


def compare_test_strategies(source_code: str) -> list[TestStrategy]:
    code_tokens = est_tokens(source_code)
    test_prompt_tokens = est_tokens(test_prompt(source_code))

    return [
        TestStrategy(
            name="local_vlm_generates_tests  (current architecture)",
            cloud_tokens=0,
            local_tokens=test_prompt_tokens,
            code_in_cloud_ctx=False,
            coverage_note="Good — VLM sees full code, generates systematic edge cases",
            latency_note="Low — no cloud round-trip",
            cost_note="Free — runs on local GPU",
        ),
        TestStrategy(
            name="cloud_llm_generates_tests",
            cloud_tokens=code_tokens + 200,   # Claude must read source to write tests
            local_tokens=0,
            code_in_cloud_ctx=True,
            coverage_note="Best reasoning about subtle invariants and corner cases",
            latency_note="High — cloud API latency + output tokens cost",
            cost_note=f"~${cloud_cost(code_tokens + 200, code_tokens // 2):.5f} per file",
        ),
        TestStrategy(
            name="cloud_reviews_local_failures  (hybrid)",
            cloud_tokens=150,    # Claude sees only FAIL summary + failing test name
            local_tokens=test_prompt_tokens,
            code_in_cloud_ctx=False,
            coverage_note="Good — local generates breadth, cloud fixes tricky failures",
            latency_note="Medium — cloud invoked only on TESTS: FAIL",
            cost_note="Near-free — cloud sees summary, not full code",
        ),
    ]


# ── Experiment 4: IDE vs Claude usage patterns ───────────────────────────────

@dataclass
class UsagePattern:
    name: str
    tool_sequence: list[str]
    cloud_tokens_session: int
    local_tokens_session: int
    context_pressure: str
    bottleneck: str
    best_for: str


def compare_usage_patterns() -> list[UsagePattern]:
    # IDE: developer issues discrete targeted tool calls
    # Example: writing a new class incrementally in JetBrains / VS Code
    ide_tool_calls = 5
    ide_cloud = ide_tool_calls * (50 + 15)   # 50 task tokens + 15 status per call
    ide_local = (
        3 * est_tokens(function_prompt("def f(x: int) -> int", "compute x²")) +
        1 * est_tokens(class_prompt("Cache", "simple cache", "get, set, clear")) +
        1 * est_tokens(test_prompt("def f(x):\n    return x**2\n"))
    )

    # Claude autonomous: one write_and_fix call, N fix iterations stay local
    claude_cloud = 100 + 30   # one tool call + "DONE after N attempts"
    claude_local = (
        est_tokens(file_prompt("Full scraper module with retries", "scraper.py")) +
        3 * est_tokens(fix_prompt("code...", "RuntimeError: connection refused"))
    )

    return [
        UsagePattern(
            name="ide_targeted_calls",
            tool_sequence=[
                "write_function(signature, desc)",
                "write_function(signature, desc)",
                "write_class(name, desc, methods)",
                "write_function(signature, desc)",
                "validate_and_test(code)",
            ],
            cloud_tokens_session=ide_cloud,
            local_tokens_session=ide_local,
            context_pressure="Low — each call is independent; no code accumulates in cloud ctx",
            bottleneck="Local VLM latency per call (N sequential round-trips to VLM)",
            best_for="Incremental development: writing one function or class at a time",
        ),
        UsagePattern(
            name="claude_autonomous_write_and_fix",
            tool_sequence=[
                "write_and_fix(description, filename)  # offline loop: generate→test→fix×N",
            ],
            cloud_tokens_session=claude_cloud,
            local_tokens_session=claude_local,
            context_pressure="Minimal — single tool call; Claude never sees the code or errors",
            bottleneck="Local VLM wall-clock time (N generate+test cycles, all offline)",
            best_for="Full-file generation with quality guarantee — fire-and-forget",
        ),
    ]


# ── Pricing helpers ───────────────────────────────────────────────────────────

# Claude Sonnet 4.6 pricing ($/1M tokens)
_INPUT_PRICE = 3.00
_OUTPUT_PRICE = 15.00


def cloud_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * _INPUT_PRICE + output_tokens * _OUTPUT_PRICE) / 1_000_000


# ── Reporting ─────────────────────────────────────────────────────────────────

def _hr(width: int = 72) -> str:
    return "─" * width


def _header(title: str) -> None:
    w = 72
    print(f"\n{'═' * w}\n  {title}\n{'═' * w}")


def report_token_savings(comparisons: list[TokenComparison]) -> None:
    _header("EXP 1 — Token Savings: Direct Claude vs MCP Offload")

    print(f"\n  Single-shot generation (cloud tokens only):\n")
    print(f"  {'Level':<10} {'Direct':>10} {'MCP cloud':>11} {'Saved':>8} {'Saved%':>8}  {'Cost saved (Sonnet 4.6)':>24}")
    print(f"  {_hr(70)}")

    for c in comparisons:
        # direct output tokens are expensive ($15/1M)
        cost_direct = cloud_cost(c.direct_cloud_in, c.direct_cloud_out)
        cost_mcp = cloud_cost(c.mcp_cloud_in + c.mcp_cloud_result, c.mcp_cloud_out)
        print(
            f"  {c.task_level:<10} "
            f"{c.direct_total:>10,} "
            f"{c.mcp_cloud_total:>11,} "
            f"{c.cloud_savings:>8,} "
            f"{c.cloud_savings_pct:>7.1f}%  "
            f"${cost_direct - cost_mcp:>22.6f}"
        )

    print(f"\n  write_and_fix loop ({comparisons[0].fix_iterations} iterations, all offline):\n")
    print(f"  {'Level':<10} {'Direct fixloop':>16} {'MCP fixloop':>13} {'Saved':>8} {'Saved%':>8}")
    print(f"  {_hr(60)}")

    for c in comparisons:
        print(
            f"  {c.task_level:<10} "
            f"{c.direct_fixloop:>16,} "
            f"{c.mcp_fixloop:>13,} "
            f"{c.fixloop_savings:>8,} "
            f"{c.fixloop_savings_pct:>7.1f}%"
        )

    print(
        "\n  Note: 'MCP cloud' counts only what Claude API charges.\n"
        "        Local VLM tokens run on-premise — zero API cost.\n"
        "        write_and_fix loop is entirely offline; cloud sees ONE tool call."
    )


def report_context_growth(steps: list[SessionStep]) -> None:
    _header("EXP 2 — Context Window Growth (5-step coding session)")

    print(f"\n  Context limit: {CONTEXT_LIMIT:,} tokens (Claude Sonnet 4.6)\n")
    print(
        f"  {'Step':<5} {'Action':<44} {'Direct ctx':>13} {'MCP ctx':>10} {'Local ctx':>10}"
    )
    print(f"  {_hr(87)}")

    for s in steps:
        direct_pct = s.direct_ctx / CONTEXT_LIMIT * 100
        mcp_pct = s.mcp_ctx / CONTEXT_LIMIT * 100
        print(
            f"  {s.step:<5} "
            f"{s.action:<44} "
            f"{s.direct_ctx:>8,} ({direct_pct:4.1f}%) "
            f"{s.mcp_ctx:>7,} ({mcp_pct:3.1f}%) "
            f"{s.local_ctx:>7,}"
        )

    last = steps[-1]
    ratio = last.direct_ctx / last.mcp_ctx
    sessions_direct = int(CONTEXT_LIMIT / last.direct_ctx)
    sessions_mcp = int(CONTEXT_LIMIT / last.mcp_ctx)

    print(
        f"\n  After {len(steps)} steps: direct ctx is {ratio:.1f}× larger than MCP ctx.\n"
        f"  Within 200K limit: ~{sessions_direct} direct sessions vs ~{sessions_mcp} MCP sessions.\n"
        f"  Local VLM ctx is stateless — resets every call, never accumulates."
    )


def report_test_strategies(strategies: list[TestStrategy], code_sample: str) -> None:
    _header("EXP 3 — Who Generates Border-Case Tests?")

    print(f"\n  Source code sample: {len(code_sample)} chars (~{est_tokens(code_sample)} tokens)\n")

    for s in strategies:
        cloud_flag = "⚠  YES — code enters cloud ctx" if s.code_in_cloud_ctx else "✓  NO  — code stays local"
        print(f"  Strategy : {s.name}")
        print(f"    Cloud tokens  : {s.cloud_tokens:,}")
        print(f"    Local tokens  : {s.local_tokens:,}")
        print(f"    Code in cloud : {cloud_flag}")
        print(f"    Coverage      : {s.coverage_note}")
        print(f"    Latency       : {s.latency_note}")
        print(f"    Cost          : {s.cost_note}")
        print()

    print(
        "  Recommendation: local_vlm_generates_tests for all routine cases.\n"
        "  Escalate to cloud only when TESTS: FAIL and the failure is non-obvious."
    )


def report_usage_patterns(patterns: list[UsagePattern]) -> None:
    _header("EXP 4 — IDE Targeted vs Claude Autonomous Usage")

    for p in patterns:
        print(f"\n  Pattern : {p.name}")
        print(f"    Tool sequence       :")
        for t in p.tool_sequence:
            print(f"      → {t}")
        print(f"    Cloud tokens/session: {p.cloud_tokens_session:,}")
        print(f"    Local tokens/session: {p.local_tokens_session:,}")
        print(f"    Context pressure    : {p.context_pressure}")
        print(f"    Primary bottleneck  : {p.bottleneck}")
        print(f"    Best for            : {p.best_for}")


# ── Live mode: actual VLM + Anthropic calls ───────────────────────────────────

async def live_token_savings(task_level: str = "function") -> None:
    """Call the running local VLM + Anthropic tokenizer for real counts."""
    try:
        import anthropic  # noqa: F401  (availability check)
        import httpx  # noqa: F401  (availability check)
    except ImportError:
        print("  Live mode requires: pip install anthropic httpx")
        return

    task = TASKS[task_level]
    _header(f"LIVE — Token savings for task level: {task_level}")

    vlm_url = f"http://127.0.0.1:{os.getenv('VLLM_PORT', '8001')}/v1/chat/completions"
    vlm_model = os.getenv("VLM_MODEL", "")
    if not vlm_model:
        print("  ERROR: set VLM_MODEL env var to the model name served by vLLM.")
        return

    if task_level == "function":
        local_prompt_text = function_prompt(task["signature"], task["description"])
    elif task_level == "class":
        local_prompt_text = class_prompt(task["name"], task["description"], task["methods"])
    else:
        local_prompt_text = file_prompt(task["description"], task["filename"])

    system_msg = "You are an expert programmer. Output only raw code with no markdown."

    # Count tokens for the direct Claude path (no MCP)
    direct_msgs = [{"role": "user", "content": f"Write {task['filename']}:\n{task['description']}"}]
    direct_in_real = count_tokens_anthropic(direct_msgs)

    # Call local VLM
    print(f"\n  Calling local VLM at {vlm_url} ...")
    t0 = time.time()
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(vlm_url, json={
            "model": vlm_model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": local_prompt_text},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
        })
        resp.raise_for_status()
        data = resp.json()

    vlm_latency = time.time() - t0
    generated_code = data["choices"][0]["message"]["content"]
    vlm_usage = data.get("usage", {})

    # Count tokens for the MCP result returned to Claude
    status_only = f"# SYNTAX_OK\n# file: {task['filename']}"
    status_with_code = f"# SYNTAX_OK\n# file: {task['filename']}\n\n{generated_code}"

    status_only_tokens = count_tokens_anthropic([{"role": "user", "content": status_only}])
    status_full_tokens = count_tokens_anthropic([{"role": "user", "content": status_with_code}])

    # Real output token estimate (Claude would have generated this)
    direct_out_est = est_tokens(generated_code)
    direct_cost = cloud_cost(direct_in_real, direct_out_est)

    tool_call_out = 30  # JSON tool call is ~30 tokens
    mcp_cloud_total_status = direct_in_real + _TOOL_SCHEMA_TOKENS + tool_call_out + status_only_tokens
    mcp_cost_status = cloud_cost(direct_in_real + _TOOL_SCHEMA_TOKENS + status_only_tokens, tool_call_out)

    print(f"\n  Generated code  : {len(generated_code):,} chars (~{est_tokens(generated_code):,} tokens)")
    print(f"  VLM latency     : {vlm_latency:.1f}s")
    print(f"  VLM usage       : {vlm_usage}")
    print(f"\n  {'Metric':<40} {'Direct':>10} {'MCP (status)':>14}")
    print(f"  {_hr(66)}")
    print(f"  {'Cloud input tokens':<40} {direct_in_real:>10,} {direct_in_real + _TOOL_SCHEMA_TOKENS:>14,}")
    print(f"  {'Cloud output tokens':<40} {direct_out_est:>10,} {tool_call_out:>14,}")
    print(f"  {'Cloud result tokens':<40} {'—':>10} {status_only_tokens:>14,}")
    print(f"  {'Total cloud tokens':<40} {direct_in_real + direct_out_est:>10,} {mcp_cloud_total_status:>14,}")
    print(f"  {'Estimated cloud cost (USD)':<40} ${direct_cost:>9.6f} ${mcp_cost_status:>13.6f}")
    print(f"\n  (If Claude reviews full code: result = {status_full_tokens:,} tokens instead of {status_only_tokens:,})")
    print(f"  Local VLM tokens: {vlm_usage} — billed at $0 (on-premise)")


# ── Main ──────────────────────────────────────────────────────────────────────

def _load_sample_code() -> str:
    """Load search.py from project root as the sample code for Exp 3."""
    candidate = Path(__file__).parent.parent / "search.py"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    return (
        "from typing import List\n\n"
        "def binary_search(sorted_list: List[int], target: int) -> int:\n"
        "    left, right = 0, len(sorted_list) - 1\n"
        "    while left <= right:\n"
        "        mid = left + (right - left) // 2\n"
        "        if sorted_list[mid] == target:\n"
        "            return mid\n"
        "        elif sorted_list[mid] < target:\n"
        "            left = mid + 1\n"
        "        else:\n"
        "            right = mid - 1\n"
        "    raise ValueError(f'Target {target} not found in the list.')\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MCP Coder Experiments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode", choices=["dry", "live"], default="dry",
        help="dry=estimate from templates (no API); live=call real VLM+Anthropic",
    )
    parser.add_argument(
        "--exp",
        choices=["all", "tokens", "context", "tests", "patterns", "live_tokens"],
        default="all",
        help="Which experiment to run (default: all)",
    )
    parser.add_argument(
        "--task", choices=list(TASKS), default="function",
        help="Task complexity level used for --exp live_tokens",
    )
    args = parser.parse_args()

    print(f"\n{'━' * 72}")
    print(f"  DockeDuck — MCP Coder Experiments  [mode={args.mode}]")
    print(f"  Pricing: Claude Sonnet 4.6 @ ${_INPUT_PRICE}/M input, ${_OUTPUT_PRICE}/M output tokens")
    print(f"  Context limit: {CONTEXT_LIMIT:,} tokens")
    print(f"{'━' * 72}")

    run_all = args.exp == "all"

    if run_all or args.exp == "tokens":
        report_token_savings(analyze_token_savings())

    if run_all or args.exp == "context":
        report_context_growth(simulate_context_growth())

    if run_all or args.exp == "tests":
        report_test_strategies(compare_test_strategies(_load_sample_code()), _load_sample_code())

    if run_all or args.exp == "patterns":
        report_usage_patterns(compare_usage_patterns())

    if args.mode == "live" and (run_all or args.exp == "live_tokens"):
        asyncio.run(live_token_savings(args.task))
    elif args.mode == "dry" and args.exp == "live_tokens":
        print("\n  --exp live_tokens requires --mode live")

    # Summary
    _header("Summary")
    comparisons = analyze_token_savings()
    steps = simulate_context_growth()
    last = steps[-1]
    ctx_ratio = last.direct_ctx / last.mcp_ctx
    module_savings = next(c for c in comparisons if c.task_level == "module")

    print(f"""
  1. TOKEN SAVINGS
     Single-shot (module-level): {module_savings.cloud_savings_pct:.0f}% fewer cloud tokens.
     write_and_fix loop        : {module_savings.fixloop_savings_pct:.0f}% fewer cloud tokens (entire fix loop is offline).
     Key insight: cloud LLM never generates code — only issues tool calls (~30 tokens out).

  2. CONTEXT GROWTH
     After 5-step session: direct context is {ctx_ratio:.1f}× larger than MCP context.
     MCP path keeps generated code out of Claude's context entirely.
     Effective: MCP extends usable session length by ~{ctx_ratio:.1f}× before hitting 200K limit.

  3. TEST STRATEGY
     Recommended: local_vlm_generates_tests (zero cloud cost, low latency).
     Escalate to cloud only when TESTS: FAIL and failure is ambiguous.

  4. USAGE PATTERNS
     IDE  → write_function / write_class targeted calls; bottleneck = VLM latency per call.
     Claude → one write_and_fix call; bottleneck = VLM fix-loop wall-clock time.
     Both patterns keep cloud token usage in the low hundreds per session.
""")


if __name__ == "__main__":
    main()
