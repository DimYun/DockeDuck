#!/usr/bin/env python3
"""
Small Context Window Experiments
=================================
Focuses on the LOCAL VLM side of the MCP architecture.
Central question: when task output exceeds MAX_TOKENS (4096), should we:

  Strategy A — whole_only    : generate everything at once (fails for large tasks),
                               validate only the final merged result
  Strategy B — syntax_chunks : generate chunk-by-chunk, syntax-check each chunk
                               immediately (cheap local ast.parse), validate
                               execution + tests only on the merged result
  Strategy C — full_chunks   : generate + fully validate each chunk independently,
                               then validate integration of the merged result

Context from .env.example (DockeDuck default):
  VLM_MAX_MODEL_LEN = 8192   total tokens per request
  MAX_TOKENS        = 4096   max output tokens
  → max prompt budget = 8192 - 4096 = 4096 tokens

Data-flow architecture:
  Task description
      │
      ▼
  ContextWindowPlanner   ← decides: one-shot or chunked
      │                 ← computes chunk order and carry-over signatures
      ├─ one-shot → VLM generate → validate
      └─ chunked  → for each chunk:
                       prompt = system + carry_over_sigs + chunk_instruction
                       VLM generate chunk
                       [optional] validate chunk syntax
                       accumulate signatures for next chunk
                   → merge chunks
                   → validate merged (execution + tests)

Run:
  python experiments/small_context_experiments.py                  # all dry
  python experiments/small_context_experiments.py --exp fit
  python experiments/small_context_experiments.py --exp carry_over
  python experiments/small_context_experiments.py --mode live --task file
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import os
import subprocess
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# ── Token helpers ─────────────────────────────────────────────────────────────

def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ── Model profiles ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ModelProfile:
    key: str
    label: str
    total_ctx: int    # VLM_MAX_MODEL_LEN
    max_output: int   # MAX_TOKENS cap
    examples: tuple[str, ...]

    @property
    def max_prompt(self) -> int:
        # tokens available for the prompt = total window minus output budget
        return self.total_ctx - self.max_output

    def output_budget(self, prompt_tokens: int) -> int:
        """Actual output tokens available given a concrete prompt size."""
        return min(self.max_output, self.total_ctx - prompt_tokens)

    def prompt_fits(self, prompt_tokens: int) -> bool:
        return prompt_tokens < self.total_ctx - 50  # 50 token safety margin


MODELS: dict[str, ModelProfile] = {
    "tiny_4k": ModelProfile(
        "tiny_4k", "tiny (4K)", 4_096, 2_048,
        ("CodeLlama-7B-4k", "Phi-1.5"),
    ),
    "small_8k": ModelProfile(
        "small_8k", "small (8K)", 8_192, 4_096,
        ("Qwen2.5-Coder-3B-AWQ", "Starcoder2-3B"),     # DockeDuck default
    ),
    "medium_16k": ModelProfile(
        "medium_16k", "medium (16K)", 16_384, 8_192,
        ("Qwen2.5-Coder-7B-AWQ", "DeepSeek-Coder-6.7B"),
    ),
    "large_32k": ModelProfile(
        "large_32k", "large (32K)", 32_768, 16_384,
        ("Qwen2.5-Coder-14B-AWQ",),
    ),
}

DEFAULT_MODEL = MODELS["small_8k"]  # matches .env.example

# ── Task definitions (mirrors mcp_experiments.py) ─────────────────────────────

_RULES = (
    "Output ONLY raw code. No markdown fences, no prose.\n"
    "Use type hints. Keep it minimal and correct."
)
_SYSTEM = "You are an expert programmer. Output only raw code with no markdown or explanation."

TASKS: dict[str, dict] = {
    "function": {
        "description": (
            "Binary search on a sorted list of integers. "
            "Return the index. Raise ValueError if not found."
        ),
        "filename": "binary_search.py",
        "code_chars": 550,
        "chunks": [
            {"name": "full_function", "desc": "Complete binary_search function with imports", "chars": 550},
        ],
    },
    "class": {
        "description": "LRU Cache with get/put in O(1) using OrderedDict.",
        "filename": "lru_cache.py",
        "code_chars": 1_800,
        "chunks": [
            {"name": "imports_and_skeleton", "desc": "Imports, LRUCache class skeleton with __init__", "chars": 400},
            {"name": "get_put_methods",      "desc": "LRUCache.get and LRUCache.put implementations", "chars": 1_200},
        ],
    },
    "file": {
        "description": (
            "Priority task queue. Tasks have: id, name, priority (1-10), payload dict. "
            "Methods: enqueue, dequeue, peek, size, clear, list_by_priority."
        ),
        "filename": "task_queue.py",
        "code_chars": 4_800,
        "chunks": [
            {"name": "imports_and_task",        "desc": "Imports and Task dataclass",                       "chars": 600},
            {"name": "queue_init_enqueue",       "desc": "TaskQueue class, __init__, enqueue",              "chars": 1_200},
            {"name": "queue_dequeue_peek_size",  "desc": "TaskQueue.dequeue, peek, size",                   "chars": 1_500},
            {"name": "queue_clear_list",         "desc": "TaskQueue.clear and list_by_priority",            "chars": 1_200},
        ],
    },
    "module": {
        "description": (
            "Config loader: ConfigError exception, ConfigSchema (validate keys/types), "
            "ConfigLoader (load from file/env). Functions: load_config, validate_config, merge_configs."
        ),
        "filename": "config_loader.py",
        "code_chars": 11_000,
        "chunks": [
            {"name": "imports_exceptions",  "desc": "Imports and ConfigError exception class",              "chars": 700},
            {"name": "config_schema",       "desc": "ConfigSchema class with validate method",             "chars": 2_200},
            {"name": "config_loader",       "desc": "ConfigLoader class (load_from_file, load_from_env)",  "chars": 3_500},
            {"name": "module_functions",    "desc": "load_config, validate_config, merge_configs functions","chars": 2_800},
        ],
    },
    # Realistic large REST API module: needs chunking on 8K AND 4K models
    "api_module": {
        "description": (
            "Complete REST API module: User SQLAlchemy model, UserSchema (Marshmallow), "
            "UserRepository (CRUD), UserService (business logic + password hashing), "
            "UserRouter (Flask/FastAPI: GET/POST/PUT/DELETE /users and /users/{id}). "
            "Full error handling, auth decorators, pagination."
        ),
        "filename": "user_api.py",
        "code_chars": 20_000,
        "chunks": [
            {"name": "imports_and_model",  "desc": "Imports, User SQLAlchemy model, UserSchema",             "chars": 2_500},
            {"name": "repository",         "desc": "UserRepository class with CRUD methods",                 "chars": 4_000},
            {"name": "service",            "desc": "UserService: business logic, password hashing",          "chars": 4_500},
            {"name": "router_read",        "desc": "UserRouter GET /users (paginated) and GET /users/{id}",  "chars": 3_500},
            {"name": "router_write",       "desc": "UserRouter POST/PUT/DELETE /users/{id}",                 "chars": 4_500},
        ],
    },
}


# ── Prompt builders ────────────────────────────────────────────────────────────

def _chunk_prompt(
    task_desc: str,
    filename: str,
    chunk_desc: str,
    carry_over: list[str],
) -> str:
    co = ""
    if carry_over:
        co = "\nAlready generated (signatures only — do NOT re-implement):\n"
        co += "\n".join(f"  {s}" for s in carry_over)
        co += "\n"
    return (
        f"File: {filename}\n"
        f"Full task: {task_desc}\n"
        f"{co}\n"
        f"Generate ONLY this part: {chunk_desc}\n"
        f"{_RULES}"
    )


def _fix_prompt(code: str, error: str) -> str:
    return (
        f"Fix this Python code error.\n\n"
        f"Code:\n{code}\n\nError:\n{error}\n\n{_RULES}"
    )


def _test_prompt(code: str) -> str:
    return (
        "Write pytest border-case tests for the code below.\n\n"
        f"{code}\n\n"
        "- Only test_* functions. Edge cases, None, boundary values.\n"
        "- Do NOT import the module — its code is already in scope.\n"
        f"{_RULES}"
    )


# ── Chunk planning ─────────────────────────────────────────────────────────────

@dataclass
class ChunkSpec:
    name: str
    description: str
    estimated_output_tokens: int

    @classmethod
    def from_dict(cls, d: dict) -> ChunkSpec:
        return cls(
            name=d["name"],
            description=d["desc"],
            estimated_output_tokens=d["chars"] // 4,
        )


@dataclass
class ChunkPlan:
    task_level: str
    model: ModelProfile
    chunks: list[ChunkSpec]
    base_prompt_tokens: int   # tokens for task_desc + system + rules (no carry-over)

    @property
    def fits_one_shot(self) -> bool:
        total_output = sum(c.estimated_output_tokens for c in self.chunks)
        # Both the MAX_TOKENS cap AND the total context window must accommodate the output
        return (
            self.base_prompt_tokens + total_output <= self.model.total_ctx
            and total_output <= self.model.max_output
        )

    @property
    def n_chunks(self) -> int:
        return 1 if self.fits_one_shot else len(self.chunks)

    def prompt_tokens_for_chunk(self, idx: int, avg_sig_tokens: int = 18) -> int:
        """Tokens in the prompt for chunk[idx], including carry-over signatures."""
        carry_over_tokens = idx * avg_sig_tokens   # one sig per previous chunk
        return self.base_prompt_tokens + carry_over_tokens

    def output_budget_for_chunk(self, idx: int, avg_sig_tokens: int = 18) -> int:
        prompt_t = self.prompt_tokens_for_chunk(idx, avg_sig_tokens)
        return self.model.output_budget(prompt_t)

    def chunk_fits(self, idx: int, avg_sig_tokens: int = 18) -> bool:
        budget = self.output_budget_for_chunk(idx, avg_sig_tokens)
        return self.chunks[idx].estimated_output_tokens <= budget


def build_plan(task_level: str, model: ModelProfile) -> ChunkPlan:
    task = TASKS[task_level]
    chunks = [ChunkSpec.from_dict(d) for d in task["chunks"]]

    # Base prompt = system hint + task_desc + filename + rules (no carry-over)
    base_prompt = (
        f"File: {task['filename']}\nFull task: {task['description']}\n{_RULES}"
    )
    base_prompt_tokens = est_tokens(_SYSTEM) + est_tokens(base_prompt)

    return ChunkPlan(
        task_level=task_level,
        model=model,
        chunks=chunks,
        base_prompt_tokens=base_prompt_tokens,
    )


# ── Validation strategies ──────────────────────────────────────────────────────

ValidationStrategy = Literal["whole_only", "syntax_per_chunk", "full_per_chunk"]

# Assumed error rates (realistic for code-generation tasks)
SYNTAX_ERROR_RATE = 0.15    # 15% of generated chunks have a syntax error
RUNTIME_ERROR_RATE = 0.25   # 25% chance of runtime error in merged result


@dataclass
class ValidationCost:
    strategy: ValidationStrategy
    task_level: str
    n_chunks: int
    total_code_tokens: int

    # Token cost for validation prompts (VLM calls to fix errors)
    expected_fix_prompt_tokens: float
    # Number of sequential VLM calls on the critical path (latency proxy)
    sequential_vlm_calls: float
    # Detection quality
    catches_chunk_syntax_early: bool
    catches_runtime_early: bool      # before merge — only strategy C
    catches_integration_errors: bool  # after merge

    @property
    def risk_label(self) -> str:
        if not self.catches_chunk_syntax_early:
            return "late error detection — wastes all previous chunk compute"
        if not self.catches_integration_errors:
            return "misses cross-chunk issues (import conflicts, name collisions)"
        return "balanced"


def compute_validation_costs(task_level: str, model: ModelProfile) -> list[ValidationCost]:
    plan = build_plan(task_level, model)
    n = plan.n_chunks
    code_tokens = TASKS[task_level]["code_chars"] // 4
    chunk_tokens = code_tokens // max(n, 1)
    error_tokens = 120  # typical error message size

    results: list[ValidationCost] = []

    # ── Strategy A: whole_only ─────────────────────────────────────────────
    # Generate all, validate merged only.
    # If it fails, the fix prompt contains the FULL merged code.
    fix_prompt_tokens_a = (
        RUNTIME_ERROR_RATE * (code_tokens + error_tokens)  # fix merged
    )
    results.append(ValidationCost(
        strategy="whole_only",
        task_level=task_level,
        n_chunks=n,
        total_code_tokens=code_tokens,
        expected_fix_prompt_tokens=fix_prompt_tokens_a,
        sequential_vlm_calls=n + RUNTIME_ERROR_RATE * 1.0,
        catches_chunk_syntax_early=False,
        catches_runtime_early=False,
        catches_integration_errors=True,
    ))

    # ── Strategy B: syntax_per_chunk ──────────────────────────────────────
    # Syntax check each chunk (local ast.parse, zero tokens).
    # Fix syntax errors in-place (fix prompt = small chunk only).
    # Validate execution + tests only on merged result.
    syntax_fix_tokens = SYNTAX_ERROR_RATE * n * (chunk_tokens + error_tokens)
    runtime_fix_tokens = RUNTIME_ERROR_RATE * (code_tokens + error_tokens)
    fix_prompt_tokens_b = syntax_fix_tokens + runtime_fix_tokens
    # syntax check is local (0 VLM calls) — only syntax fixes add VLM calls
    syntax_fix_calls = SYNTAX_ERROR_RATE * n
    results.append(ValidationCost(
        strategy="syntax_per_chunk",
        task_level=task_level,
        n_chunks=n,
        total_code_tokens=code_tokens,
        expected_fix_prompt_tokens=fix_prompt_tokens_b,
        sequential_vlm_calls=n + syntax_fix_calls + RUNTIME_ERROR_RATE * 1.0,
        catches_chunk_syntax_early=True,
        catches_runtime_early=False,
        catches_integration_errors=True,
    ))

    # ── Strategy C: full_per_chunk ─────────────────────────────────────────
    # Fully validate (syntax + run stub) each chunk before proceeding.
    # Fix errors with small chunk-scoped fix prompts.
    # Then validate integration of merged result.
    per_chunk_runtime_rate = 0.10  # lower than merged because stubs are simpler
    stub_fix_tokens = (SYNTAX_ERROR_RATE + per_chunk_runtime_rate) * n * (chunk_tokens + error_tokens)
    integration_fix_tokens = RUNTIME_ERROR_RATE * 0.5 * (code_tokens + error_tokens)  # fewer surprises
    fix_prompt_tokens_c = stub_fix_tokens + integration_fix_tokens
    fix_calls_c = (SYNTAX_ERROR_RATE + per_chunk_runtime_rate) * n + RUNTIME_ERROR_RATE * 0.5
    results.append(ValidationCost(
        strategy="full_per_chunk",
        task_level=task_level,
        n_chunks=n,
        total_code_tokens=code_tokens,
        expected_fix_prompt_tokens=fix_prompt_tokens_c,
        sequential_vlm_calls=n + fix_calls_c,
        catches_chunk_syntax_early=True,
        catches_runtime_early=True,
        catches_integration_errors=True,
    ))

    return results


# ── Carry-over context model ───────────────────────────────────────────────────

@dataclass
class CarryOverStep:
    chunk_idx: int
    chunk_name: str
    carry_over_tokens: int      # tokens added to prompt for this chunk vs chunk 0
    total_prompt_tokens: int    # total prompt size for this chunk
    output_budget: int          # tokens available for output
    chunk_output_tokens: int    # estimated output needed
    fits: bool                  # does output fit in budget?


def model_carry_over(task_level: str, model: ModelProfile, avg_sig_tokens: int = 18) -> list[CarryOverStep]:
    plan = build_plan(task_level, model)
    steps: list[CarryOverStep] = []

    for i, chunk in enumerate(plan.chunks):
        carry = i * avg_sig_tokens
        total_prompt = plan.base_prompt_tokens + carry
        budget = model.output_budget(total_prompt)
        fits = chunk.estimated_output_tokens <= budget
        steps.append(CarryOverStep(
            chunk_idx=i,
            chunk_name=chunk.name,
            carry_over_tokens=carry,
            total_prompt_tokens=total_prompt,
            output_budget=budget,
            chunk_output_tokens=chunk.estimated_output_tokens,
            fits=fits,
        ))

    return steps


# ── Experiment 1: Fit analysis ─────────────────────────────────────────────────

FitStatus = Literal["one_shot", "chunked", "impossible"]


def fit_status(task_level: str, model: ModelProfile) -> FitStatus:
    plan = build_plan(task_level, model)
    total_output = sum(c.estimated_output_tokens for c in plan.chunks)

    # One-shot: full output fits within MAX_TOKENS cap AND total context window
    if total_output <= model.max_output and plan.base_prompt_tokens + total_output <= model.total_ctx:
        return "one_shot"

    # Chunked: each individual chunk fits within its own output budget (with carry-over)
    steps = model_carry_over(task_level, model)
    if all(s.fits for s in steps):
        return "chunked"

    # Even a single chunk overflows the output budget — need a larger model
    return "impossible"


def exp_fit_analysis() -> None:
    _header("EXP 1 — Fit Analysis: Task × Model Context Window")

    task_levels = list(TASKS.keys())
    model_keys = list(MODELS.keys())

    # Header
    print(f"\n  {'Task level':<14}", end="")
    for mk in model_keys:
        m = MODELS[mk]
        print(f"  {m.label:<16}", end="")
    print()
    print(f"  {'code size (chars)':<14}", end="")
    for mk in model_keys:
        m = MODELS[mk]
        print(f"  {'total/output':>16}", end="")
    print()
    print(f"  {'-' * (14 + len(model_keys) * 18)}")

    status_symbols = {"one_shot": "✓ one-shot", "chunked": "⊕ chunked", "impossible": "✗ impossible"}

    for tl in task_levels:
        chars = TASKS[tl]["code_chars"]
        n_chunks = len(TASKS[tl]["chunks"])
        print(f"\n  {tl:<12} ({chars:>5}c)", end="")
        for mk in model_keys:
            m = MODELS[mk]
            status = fit_status(tl, m)
            sym = status_symbols[status]
            suffix = f" ×{n_chunks}" if status == "chunked" else ""
            print(f"  {sym + suffix:<16}", end="")
        print()

    print(f"\n  DockeDuck default: {DEFAULT_MODEL.label}  "
          f"(total={DEFAULT_MODEL.total_ctx}, max_out={DEFAULT_MODEL.max_output})")
    print(f"  ✓ one-shot  = full file generated in a single VLM call")
    print(f"  ⊕ chunked   = split into N chunk calls, each fits in window")
    print(f"  ✗ impossible= even a single chunk exceeds the output budget")


# ── Experiment 2: Chunk plans ─────────────────────────────────────────────────

def exp_chunk_plans(model_key: str = "small_8k") -> None:
    model = MODELS[model_key]
    _header(f"EXP 2 — Chunk Plans for {model.label}")
    print(f"  Prompt budget: {model.max_prompt:,} tokens   Output budget: {model.max_output:,} tokens\n")

    for tl, task in TASKS.items():
        plan = build_plan(tl, model)
        status = fit_status(tl, model)
        chunks = plan.chunks

        print(f"  ── {tl:<10} ({task['code_chars']:,} chars ≈ {task['code_chars'] // 4:,} tokens)  [{status}]")

        if status == "one_shot":
            total_out = sum(c.estimated_output_tokens for c in chunks)
            print(f"     Single call: prompt={plan.base_prompt_tokens} + output≈{total_out} "
                  f"= {plan.base_prompt_tokens + total_out} / {model.total_ctx} total  "
                  f"(max_output cap: {model.max_output})")
        else:
            steps = model_carry_over(tl, model)
            print(f"     {'Chunk':<28} {'carry-over':>12} {'prompt':>8} {'out_need':>10} {'budget':>8} {'fits':>6}")
            print(f"     {'-' * 75}")
            for s in steps:
                fits_mark = "✓" if s.fits else "✗ OVERFLOW"
                print(
                    f"     {s.chunk_name:<28} "
                    f"{s.carry_over_tokens:>12} "
                    f"{s.total_prompt_tokens:>8} "
                    f"{s.chunk_output_tokens:>10} "
                    f"{s.output_budget:>8} "
                    f"{fits_mark:>6}"
                )
        print()


# ── Experiment 3: Validation strategy comparison ──────────────────────────────

def exp_validation_strategies(model_key: str = "small_8k") -> None:
    model = MODELS[model_key]
    _header("EXP 3 — Validation Strategies: A / B / C")
    print(
        "  A = whole_only       : generate all chunks, validate merged result only\n"
        "  B = syntax_per_chunk : syntax-check each chunk (local ast.parse), "
        "validate execution+tests on merged\n"
        "  C = full_per_chunk   : fully validate (syntax+execution stub) each chunk, "
        "then integration test\n"
    )

    strategy_labels = {
        "whole_only": "A — whole_only",
        "syntax_per_chunk": "B — syntax/chunk",
        "full_per_chunk": "C — full/chunk",
    }

    for tl in TASKS:
        plan = build_plan(tl, model)
        status = fit_status(tl, model)
        costs = compute_validation_costs(tl, model)
        n = plan.n_chunks

        print(f"  ── {tl}  [{status}, {n} VLM call(s)]")
        print(f"     {'Strategy':<22} {'fix prompt toks':>16} {'seq VLM calls':>15} "
              f"{'early syntax':>13} {'early runtime':>14} {'integration':>13} {'risk'}")
        print(f"     {'-' * 110}")

        for c in costs:
            print(
                f"     {strategy_labels[c.strategy]:<22} "
                f"{c.expected_fix_prompt_tokens:>16.0f} "
                f"{c.sequential_vlm_calls:>15.1f} "
                f"{'✓' if c.catches_chunk_syntax_early else '✗':>13} "
                f"{'✓' if c.catches_runtime_early else '✗':>14} "
                f"{'✓' if c.catches_integration_errors else '✗':>13}  "
                f"{c.risk_label}"
            )
        print()

    print(
        "  Recommendation for DockeDuck (8K window):\n"
        "    Use Strategy B (syntax_per_chunk):\n"
        "      - Syntax check is local (zero VLM cost, <1ms via ast.parse)\n"
        "      - Fix prompts operate on small chunks (~chunk_tokens + error) not the full file\n"
        "      - Integration validated once on merged result — catches cross-chunk issues\n"
        "      - Strategy C overhead is not justified: per-chunk execution stubs are fragile\n"
        "        (stub cannot reproduce real imports/dependencies from other chunks)"
    )


# ── Experiment 4: Carry-over token growth ─────────────────────────────────────

def exp_carry_over(model_key: str = "small_8k") -> None:
    model = MODELS[model_key]
    _header(f"EXP 4 — Carry-Over Context Growth ({model.label})")
    print(
        "  Each chunk prompt includes signatures of all previously generated components\n"
        "  (names + type hints only, NOT full bodies).\n"
        "  Average signature ≈ 18 tokens  (e.g. 'def enqueue(self, task: Task) -> None')\n"
    )

    for tl in ["class", "file", "module", "api_module"]:
        task = TASKS[tl]
        status = fit_status(tl, model)
        if status == "one_shot":
            print(f"  {tl}: one-shot — no carry-over needed\n")
            continue

        steps = model_carry_over(tl, model)
        print(f"  ── {tl}  (total {task['code_chars']:,} chars, {len(steps)} chunks)")
        print(f"     {'Chunk':<28} {'carry tokens':>13} {'prompt total':>13} "
              f"{'out budget':>11} {'out needed':>11} {'headroom':>10}")
        print(f"     {'-' * 90}")

        for s in steps:
            headroom = s.output_budget - s.chunk_output_tokens
            headroom_label = f"+{headroom}" if headroom >= 0 else f"OVERFLOW {headroom}"
            print(
                f"     {s.chunk_name:<28} "
                f"{s.carry_over_tokens:>13} "
                f"{s.total_prompt_tokens:>13} "
                f"{s.output_budget:>11} "
                f"{s.chunk_output_tokens:>11} "
                f"{headroom_label:>10}"
            )

        last = steps[-1]
        total_carry = last.carry_over_tokens
        base = steps[0].total_prompt_tokens
        pct = total_carry / base * 100
        print(f"\n     Carry-over growth: +{total_carry} tokens over {len(steps)} chunks "
              f"(+{pct:.0f}% of base prompt).\n")

    print(
        "  Key insight: carry-over grows linearly with chunk count but stays small\n"
        "  because we carry only SIGNATURES (18 tokens avg), NOT full implementations.\n"
        "  Even a 10-chunk task adds only ~180 tokens of carry-over."
    )


# ── Live mode: chunked generation via local VLM ───────────────────────────────

def _check_syntax(code: str) -> tuple[bool, str]:
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, str(e)


def _run_code(code: str, timeout: int = 20) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        tmp = f.name
    try:
        r = subprocess.run(
            ["python3", tmp], capture_output=True, text=True, timeout=timeout
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    finally:
        Path(tmp).unlink(missing_ok=True)


def _run_tests(source: str, tests: str, timeout: int = 40) -> tuple[bool, str]:
    combined = f"{source}\n\n{tests}"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(combined)
        tmp = f.name
    try:
        r = subprocess.run(
            ["python3", "-m", "pytest", tmp, "-v", "--tb=short", "--no-header"],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return _run_code(combined, timeout)
    finally:
        Path(tmp).unlink(missing_ok=True)


async def _vlm_call(prompt: str, system: str = _SYSTEM) -> str:
    import httpx
    url = f"http://127.0.0.1:{os.getenv('VLLM_PORT', '8001')}/v1/chat/completions"
    model = os.getenv("VLM_MODEL", "")
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(url, json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(os.getenv("TEMPERATURE", "0.1")),
            "max_tokens": int(os.getenv("MAX_TOKENS", "4096")),
        })
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def live_chunked_generation(task_level: str, strategy: ValidationStrategy) -> None:
    """Run Strategy B (syntax_per_chunk) chunked generation against local VLM."""
    try:
        import httpx  # noqa: F401
    except ImportError:
        print("  Live mode requires: pip install httpx")
        return

    task = TASKS[task_level]
    model = DEFAULT_MODEL  # from env
    plan = build_plan(task_level, model)
    status = fit_status(task_level, model)

    _header(f"LIVE — {task_level} | strategy={strategy} | [{status}]")
    print(f"  Filename : {task['filename']}")
    print(f"  Model    : {os.getenv('VLM_MODEL', '(VLM_MODEL not set)')}")
    print(f"  Window   : {model.total_ctx} total / {model.max_output} max output\n")

    generated_chunks: list[str] = []
    carry_over_sigs: list[str] = []
    total_t = 0.0
    vlm_calls = 0

    for i, chunk_spec in enumerate(plan.chunks if status != "one_shot" else plan.chunks[:1]):
        chunk_desc = (
            chunk_spec.description
            if status != "one_shot"
            else f"Complete {task['filename']} — {task['description']}"
        )
        prompt = _chunk_prompt(task["description"], task["filename"], chunk_desc, carry_over_sigs)
        prompt_tokens = est_tokens(_SYSTEM) + est_tokens(prompt)
        out_budget = model.output_budget(prompt_tokens)

        print(f"  [chunk {i+1}/{plan.n_chunks}] {chunk_spec.name}")
        print(f"    prompt={prompt_tokens} tokens  output_budget={out_budget} tokens")

        t0 = time.time()
        code = await _vlm_call(prompt)
        elapsed = time.time() - t0
        vlm_calls += 1
        total_t += elapsed

        out_tokens = est_tokens(code)
        print(f"    generated: {len(code)} chars (~{out_tokens} tokens)  in {elapsed:.1f}s")

        # ── Per-chunk syntax check (Strategy B and C) ─────────────────────
        if strategy in ("syntax_per_chunk", "full_per_chunk"):
            ok, err = _check_syntax(code)
            if ok:
                print(f"    syntax: ✓ OK")
            else:
                print(f"    syntax: ✗ {err[:80]}")
                print(f"    fixing syntax...")
                fix = _fix_prompt(code, f"SyntaxError: {err}")
                t0 = time.time()
                code = await _vlm_call(fix)
                elapsed2 = time.time() - t0
                vlm_calls += 1
                total_t += elapsed2
                ok2, err2 = _check_syntax(code)
                print(f"    fix result: {'✓ OK' if ok2 else '✗ still broken'} in {elapsed2:.1f}s")

        # ── Per-chunk execution stub (Strategy C only) ────────────────────
        if strategy == "full_per_chunk":
            ok, out = _run_code(code)
            print(f"    execution stub: {'✓' if ok else '✗ ' + out[:60]}")

        # Accumulate signature for carry-over
        # Extract first line of each def/class from the generated code
        for line in code.splitlines():
            stripped = line.strip()
            if (stripped.startswith("def ") or stripped.startswith("class ")) and ":" in stripped:
                carry_over_sigs.append(stripped.rstrip(":"))
                break

        generated_chunks.append(code)
        print()

    # ── Merge ─────────────────────────────────────────────────────────────
    merged = "\n\n".join(generated_chunks)
    print(f"  Merged: {len(merged)} chars (~{est_tokens(merged)} tokens)")

    # ── Validate merged result ─────────────────────────────────────────────
    print(f"\n  Validating merged result...")
    ok, err = _check_syntax(merged)
    print(f"    SYNTAX:    {'OK' if ok else 'FAIL — ' + err[:80]}")

    if ok:
        ok, out = _run_code(merged)
        print(f"    EXECUTION: {'OK' if ok else 'FAIL'}")
        if out:
            print(textwrap.indent(out[:300], "      "))

        print(f"    TESTS:     generating via VLM...")
        t0 = time.time()
        test_code = await _vlm_call(_test_prompt(merged))
        vlm_calls += 1
        total_t += time.time() - t0

        ok, out = _run_tests(merged, test_code)
        print(f"    TESTS:     {'PASS ✓' if ok else 'FAIL ✗'}")
        if out:
            # Show only the summary line
            for line in out.splitlines()[-5:]:
                if line.strip():
                    print(f"      {line}")

    print(f"\n  Total VLM calls : {vlm_calls}")
    print(f"  Total wall time : {total_t:.1f}s")
    print(f"  Avg per call    : {total_t / vlm_calls:.1f}s")


# ── Reporting helpers ──────────────────────────────────────────────────────────

def _header(title: str, w: int = 72) -> None:
    print(f"\n{'═' * w}\n  {title}\n{'═' * w}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Small Context Window Experiments for MCP Coder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode", choices=["dry", "live"], default="dry",
        help="dry=analysis only; live=call running local VLM",
    )
    parser.add_argument(
        "--exp",
        choices=["all", "fit", "plans", "validation", "carry_over", "live_gen"],
        default="all",
    )
    parser.add_argument("--model", choices=list(MODELS), default="small_8k",
                        help="Model profile for plan/validation/carry_over experiments")
    parser.add_argument("--task", choices=list(TASKS), default="file",
                        help="Task level for live generation")
    parser.add_argument(
        "--strategy",
        choices=["whole_only", "syntax_per_chunk", "full_per_chunk"],
        default="syntax_per_chunk",
        help="Validation strategy for live generation",
    )
    args = parser.parse_args()

    print(f"\n{'━' * 72}")
    print(f"  DockeDuck — Small Context Window Experiments  [mode={args.mode}]")
    print(f"  DockeDuck default model: {DEFAULT_MODEL.label}")
    print(f"    VLM_MAX_MODEL_LEN={DEFAULT_MODEL.total_ctx}  MAX_TOKENS={DEFAULT_MODEL.max_output}")
    print(f"    Prompt budget = {DEFAULT_MODEL.max_prompt} tokens")
    print(f"{'━' * 72}")

    run_all = args.exp == "all"

    if run_all or args.exp == "fit":
        exp_fit_analysis()

    if run_all or args.exp == "plans":
        exp_chunk_plans(args.model)

    if run_all or args.exp == "validation":
        exp_validation_strategies(args.model)

    if run_all or args.exp == "carry_over":
        exp_carry_over(args.model)

    if args.mode == "live" and (run_all or args.exp == "live_gen"):
        asyncio.run(live_chunked_generation(args.task, args.strategy))
    elif args.mode == "dry" and args.exp == "live_gen":
        print("\n  --exp live_gen requires --mode live")

    # ── Summary ────────────────────────────────────────────────────────────
    _header("Summary — Data Flow Decision Tree")
    m = DEFAULT_MODEL
    print(f"""
  Given a local VLM with total_ctx={m.total_ctx}, max_output={m.max_output}:

  1. ESTIMATE task output tokens before calling VLM
       if prompt_tokens + estimated_output <= {m.total_ctx}:
           → ONE-SHOT: single VLM call, validate whole result
       elif all chunk outputs fit individually:
           → CHUNKED (Strategy B): N sequential calls, syntax-check each chunk
       else:
           → IMPOSSIBLE: task chunk is too large even for a single window
             → options: (a) use a larger context model, (b) break task further

  2. CARRY-OVER between chunks:
       pass only function/class SIGNATURES from previous chunks (avg 18 tokens each)
       do NOT pass full code bodies — that defeats the purpose of chunking

  3. VALIDATION after each chunk (Strategy B):
       ast.parse(chunk_code)            ← local, zero VLM tokens, <1ms
       if SyntaxError:
           fix prompt = chunk_code + error  ← small prompt, cheap to fix

  4. VALIDATION after merge:
       run_code(merged)                 ← catches runtime errors
       run_tests(merged, test_code)     ← catches logic + integration errors
       fix prompt = merged + error      ← large prompt, but rare in practice

  Why NOT Strategy A (validate only the merged result)?
       If chunk 1 of 4 has a syntax error, you waste 3 more VLM calls before
       finding out. Syntax check is local (ast.parse), so there is no reason
       not to check immediately after each chunk.

  Why NOT Strategy C (full per-chunk execution)?
       Executing chunk N in isolation requires stubbing imports and dependencies
       from chunks 1..N-1 — complex to automate and often wrong. The marginal
       benefit over Strategy B is low given the added complexity.
    """)


if __name__ == "__main__":
    main()
