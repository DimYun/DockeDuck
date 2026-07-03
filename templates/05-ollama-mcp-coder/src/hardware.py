"""Hardware detection + benchmark-informed model / context recommendations (Ollama).

Backs the `recommend_model` and `recommend_context_window` MCP tools. Unlike vLLM, Ollama
runs GGUF models on **CPU** as well as GPU, and honours a per-request context (num_ctx) — so
these recommendations can be applied without a restart. Numbers come from the 6 GB benchmark
(experiments/RESULTS.md) plus KV-cache math.
"""
from __future__ import annotations

import shutil
import subprocess

# Ollama catalogue (GGUF Q4_K_M). ctx_6gb = the context we validated on a 6 GB GPU.
# quality = measured local 3-gate confidence; rescue = tasks passing all gates with one
# Claude rescue. weights_gb = loaded footprint; kv_* kept for the "KB/token" explanation.
MODELS = [
    {
        "tag": "qwen2.5-coder:3b",
        "weights_gb": 2.0, "ctx_6gb": 16384, "layers": 36, "kv_heads": 2, "head_dim": 128,
        "rope_max": 32768, "quality": 85, "rescue": "5/5", "thinking": False,
        "note": "fast, reliable, 100% with one rescue — the recommended default",
    },
    {
        "tag": "qwen3.5:4b",
        "weights_gb": 3.4, "ctx_6gb": 12288, "layers": 36, "kv_heads": 8, "head_dim": 128,
        "rope_max": 32768, "quality": 80, "rescue": "5/5", "thinking": True,
        "note": "thinking-capable (ENABLE_THINKING=true → 87% local, 100% rescue); slower",
    },
    {
        "tag": "qwen2.5-coder:1.5b",
        "weights_gb": 1.0, "ctx_6gb": 16384, "layers": 28, "kv_heads": 2, "head_dim": 128,
        "rope_max": 32768, "quality": 70, "rescue": "5/5", "thinking": False,
        "note": "smallest — best for CPU-only or ≤3 GB GPUs; still 100% with rescue",
    },
]

_UTIL = 0.88
_OVERHEAD_GB = 0.3
_MIN_CTX = 2048


def detect_hardware() -> dict:
    """Return {'gpu': bool, 'vram_gb': float, 'ram_gb': float, 'name': str}."""
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip().splitlines()
            if out:
                mib, name = out[0].split(",", 1)
                return {"gpu": True, "vram_gb": round(int(mib.strip()) / 1024, 1),
                        "ram_gb": _ram_gb(), "name": name.strip()}
        except (subprocess.SubprocessError, ValueError):
            pass
    return {"gpu": False, "vram_gb": 0.0, "ram_gb": _ram_gb(), "name": "CPU only"}


def _ram_gb() -> float:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / (1024 ** 2), 1)  # kB → GB
    except OSError:
        pass
    return 0.0


def _kv_avail_gb(model: dict, mem_gb: float) -> float:
    return mem_gb * _UTIL - model["weights_gb"] - _OVERHEAD_GB


def context_for(model: dict, mem_gb: float) -> int:
    """Largest num_ctx (multiple of 2048) that fits `mem_gb` (VRAM on GPU, or RAM on CPU),
    anchored to the value validated at 6 GB and scaled by available KV memory, capped at the
    model's RoPE maximum. (On CPU there is plenty of RAM but inference is slow, so a smaller
    context is usually the better choice in practice.)"""
    base = _kv_avail_gb(model, 6.0)
    here = _kv_avail_gb(model, mem_gb)
    if base <= 0 or here <= 0:
        return 0
    ctx = int(model["ctx_6gb"] * here / base)
    return max(0, min((ctx // 2048) * 2048, model["rope_max"]))


def _mem(hw: dict) -> float:
    return hw["vram_gb"] if hw["gpu"] else hw["ram_gb"]


def fits(model: dict, hw: dict) -> bool:
    return context_for(model, _mem(hw)) >= _MIN_CTX


def recommend_model(prefer: str = "quality") -> dict:
    """Pick the best Ollama model for the detected GPU (or CPU/RAM).

    prefer: 'quality' (default) · 'context' (biggest window) · 'speed' (smallest model).
    """
    hw = detect_hardware()
    usable = [m for m in MODELS if fits(m, hw)]
    if not usable:
        return {"tag": None, "hw": hw,
                "reason": "Not even the 1.5B model fits — free some memory and retry."}
    if prefer == "context":
        best = max(usable, key=lambda m: context_for(m, _mem(hw)))
    elif prefer == "speed":
        best = min(usable, key=lambda m: m["min_vram_gb"])
    else:
        best = max(usable, key=lambda m: m["quality"])
    return {
        "tag": best["tag"], "hw": hw, "recommended_context": context_for(best, _mem(hw)),
        "quality": best["quality"], "rescue": best["rescue"], "thinking": best["thinking"],
        "note": best["note"],
    }
