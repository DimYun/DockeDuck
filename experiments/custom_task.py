#!/usr/bin/env python3
"""
Custom Task Benchmark — v2 (user-YAML architecture)
===================================================
Run ONE user-defined task spec through the same backends as bench_real.py.

This is a thin driver on top of bench_real.py: it loads an arbitrary spec file
(any path, not only the built-in experiments/tasks/*.yaml registry) and runs the
selected backends against it. The flow is identical to the main benchmark:

    conditions → local tests → local code → fix loop → [Claude rescue if needed]

Spec format (YAML or JSON):
    name          (optional) display name
    filename      output file the model must write (e.g. rate_limiter.py)
    language      python | dockerfile | yaml | json | ...  (default: python)
    type          function | class | connected functions | module | project
    description   what to implement
    conditions    natural-language test cases  (local model → pytest)   OR
    tests         pre-written pytest code (used directly, no test-gen step)

Backends:
    local_vllm          — vLLM, zero cloud cost
    local_ollama        — Ollama, zero cloud cost
    local_vllm_rescue   — vLLM + Claude rescue only on failure
    local_ollama_rescue — Ollama + Claude rescue only on failure
    claude_direct       — Claude does everything (cloud baseline)

Usage:
    # Local vLLM only (needs `make start-bench && make wait-ready`):
    python experiments/custom_task.py --task experiments/tasks/function-example.yaml

    # Ollama + Claude rescue, save CSV:
    python experiments/custom_task.py --task my_task.yaml \\
        --backend local_ollama_rescue claude_direct \\
        --output experiments/results/custom.csv

Prerequisites:
    local_vllm*   → container via `make start-bench` (vLLM on port 8001)
    local_ollama* → Ollama running (OLLAMA_URL, OLLAMA_MODEL)
    *_rescue / claude_direct → ANTHROPIC_API_KEY in the environment
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# ── Import shared machinery from bench_real (same experiments/ directory) ─────
sys.path.insert(0, str(Path(__file__).parent))
from bench_real import (  # noqa: E402
    CLAUDE_INPUT_PRICE,
    CLAUDE_OUTPUT_PRICE,
    CLAUDE_MODEL,
    BenchResult,
    _detect_model,
    measure_claude_direct,
    measure_local_ollama,
    measure_local_ollama_rescue,
    measure_local_vllm,
    measure_local_vllm_rescue,
    save_csv,
)

_W = 100


# ── Spec loader (arbitrary path) ──────────────────────────────────────────────

def load_spec(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Task spec not found: {path}")
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        import yaml
        spec = yaml.safe_load(text)
    else:
        spec = json.loads(text)
    # Legacy compatibility: an English `border_cases:` list maps onto `conditions:`.
    if "border_cases" in spec and "conditions" not in spec:
        cases = spec["border_cases"]
        spec["conditions"] = (
            "\n".join(f"- {c}" for c in cases) if isinstance(cases, list) else str(cases)
        )
    return spec


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_results(results: list[BenchResult], spec: dict) -> None:
    name = spec.get("name", spec.get("filename", "task"))
    print(f"\n{'━' * _W}")
    print(f"  CUSTOM TASK RESULTS  —  {name}  ({spec.get('filename', '')})")
    print(f"{'━' * _W}\n")
    print(
        f"  {'Backend':<22} {'Time':>7} "
        f"{'Cld-In':>8} {'Cld-Out':>8} {'Lcl-In':>8} {'Lcl-Out':>8} "
        f"{'Cost USD':>10}  {'Quality':<26}  {'Fixes':>5}"
    )
    print(f"  {'─' * (_W - 2)}")
    for r in results:
        print(
            f"  {r.backend:<22} {r.time_s:>6.1f}s "
            f"{r.cloud_in_tok:>8,} {r.cloud_out_tok:>8,} "
            f"{r.local_in_tok:>8,} {r.local_out_tok:>8,} "
            f"${r.cloud_cost_usd:>9.5f}  "
            f"{r.quality_label():<26}  {r.fix_iters:>5}"
        )
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

BACKEND_CHOICES = [
    "local_vllm", "local_ollama",
    "local_vllm_rescue", "local_ollama_rescue",
    "claude_direct", "all",
]


async def async_main(args: argparse.Namespace) -> None:
    spec = load_spec(args.task)
    name = spec.get("name", Path(args.task).stem)

    if "all" in args.backend:
        backends = ["local_vllm", "local_vllm_rescue", "claude_direct"]
    else:
        backends = args.backend

    needs_vllm   = any("vllm" in b for b in backends)
    needs_ollama = any("ollama" in b for b in backends)
    needs_claude = any(b == "claude_direct" or "rescue" in b for b in backends)

    vlm_url      = args.vlm_url.rstrip("/")
    vlm_model    = os.getenv("VLM_MODEL", "")
    ollama_url   = args.ollama_url.rstrip("/")
    ollama_model = os.getenv("OLLAMA_MODEL", "")
    num_ctx      = int(os.getenv("OLLAMA_NUM_CTX", "16384"))

    print(f"\n{'━' * _W}")
    print(f"  DockeDuck — Custom Task Benchmark v2")
    print(f"  task     = {name}  ({spec.get('filename', '')})")
    print(f"  backends = {backends}")
    print(f"{'━' * _W}\n")

    # Pre-flight checks
    if needs_vllm:
        detected = await _detect_model(vlm_url)
        if detected is None:
            print(f"  ERROR: vLLM not reachable at {vlm_url}\n  Run: make start-bench && make wait-ready")
            return
        vlm_model = vlm_model or detected
    if needs_ollama:
        detected = await _detect_model(ollama_url)
        if detected is None:
            print(f"  ERROR: Ollama not reachable at {ollama_url}")
            return
        ollama_model = ollama_model or detected
    if needs_claude and not os.getenv("ANTHROPIC_API_KEY"):
        print("  ERROR: ANTHROPIC_API_KEY not set (needed for claude_direct / *_rescue)")
        return

    if "conditions" not in spec and "tests" not in spec:
        print("  ERROR: spec must have 'conditions:' (natural language) or 'tests:' (pytest code)")
        return

    results: list[BenchResult] = []
    for backend in backends:
        print(f"  [{backend:<22}] {spec.get('filename', '')} ... ", end="", flush=True)
        try:
            if backend == "local_vllm":
                r = await measure_local_vllm(name, vlm_url, vlm_model, args.max_retries, spec=spec)
            elif backend == "local_ollama":
                r = await measure_local_ollama(name, ollama_url, ollama_model, args.max_retries, num_ctx, spec=spec)
            elif backend == "local_vllm_rescue":
                r = await measure_local_vllm_rescue(name, vlm_url, vlm_model, args.max_retries, spec=spec)
            elif backend == "local_ollama_rescue":
                r = await measure_local_ollama_rescue(name, ollama_url, ollama_model, args.max_retries, num_ctx, spec=spec)
            elif backend == "claude_direct":
                r = await measure_claude_direct(name, args.max_retries, spec=spec)
            else:
                continue
            print(r.quality_label())
        except Exception as e:  # noqa: BLE001
            r = BenchResult(backend=backend, task_level=name, error=str(e))
            print(f"ERROR: {e}")
        results.append(r)

    if results:
        print_results(results, spec)
    if args.output and results:
        save_csv(results, args.output)
        print(f"  CSV → {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark a single user-defined task spec (bench_real v2 backends).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Claude pricing comes from CLAUDE_MODEL / CLAUDE_INPUT_PRICE / CLAUDE_OUTPUT_PRICE\n"
            f"(current: {CLAUDE_MODEL} @ ${CLAUDE_INPUT_PRICE}/M in, ${CLAUDE_OUTPUT_PRICE}/M out)."
        ),
    )
    parser.add_argument("--task", required=True, help="Path to a task spec YAML/JSON")
    parser.add_argument("--backend", nargs="+", choices=BACKEND_CHOICES, default=["local_vllm"])
    parser.add_argument("--vlm-url",    default=os.getenv("VLM_URL",    "http://localhost:8001/v1"))
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://localhost:11434/v1"))
    parser.add_argument("--max-retries", type=int, default=6)
    parser.add_argument("--output", metavar="FILE", help="Save results to CSV")
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
