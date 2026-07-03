"""Hardware detection + benchmark-informed model / context recommendations.

Backs the `recommend_model` and `recommend_context_window` MCP tools: a cloud LLM (or the
user) calls them to pick the best vLLM model and the largest context window that fits the
detected GPU. Numbers come from the 6 GB benchmark in experiments/RESULTS.md plus the exact
KV-cache math (context is bounded by VRAM and the model's GQA KV-head count, not param size).
"""
from __future__ import annotations

import shutil
import subprocess

# vLLM catalogue (AWQ 4-bit). ctx_6gb = the context we actually validated on a 6 GB GPU
# (KV-head-bound; the 8-head 4B holds far less than the 2-head coders). quality = measured
# local 3-gate confidence (RESULTS.md §1); rescue = tasks passing all gates with one rescue.
# kv_heads/layers/head_dim are kept for the "KB/token" explanation only.
MODELS = [
    {
        "name": "Qwen/Qwen3-4B-AWQ",
        "weights_gb": 2.6, "ctx_6gb": 8192, "layers": 36, "kv_heads": 8, "head_dim": 128,
        "rope_max": 32768, "quality": 93, "rescue": "5/5", "thinking": True,
        "note": "best quality; thinking-capable (ENABLE_THINKING=true → 5/5 local)",
    },
    {
        "name": "Qwen/Qwen2.5-Coder-3B-Instruct-AWQ",
        "weights_gb": 2.1, "ctx_6gb": 24576, "layers": 36, "kv_heads": 2, "head_dim": 128,
        "rope_max": 32768, "quality": 80, "rescue": "5/5", "thinking": False,
        "note": "code-specialised; 2 KV heads → far bigger usable context",
    },
    {
        "name": "Qwen/Qwen2.5-Coder-1.5B-Instruct-AWQ",
        "weights_gb": 1.1, "ctx_6gb": 32768, "layers": 28, "kv_heads": 2, "head_dim": 128,
        "rope_max": 32768, "quality": 80, "rescue": "4/5", "thinking": False,
        "note": "smallest/fastest; good for ≤4 GB GPUs",
    },
    {
        "name": "Qwen/Qwen2.5-Coder-7B-Instruct-AWQ",
        "weights_gb": 4.7, "ctx_6gb": 2048, "layers": 28, "kv_heads": 4, "head_dim": 128,
        "rope_max": 32768, "quality": 90, "rescue": "5/5", "thinking": False,
        "note": "strongest coder; needs >6 GB for a usable context window",
    },
]

_UTIL = 0.88          # effective usable fraction (weights + KV + activation/CUDA overhead)
_OVERHEAD_GB = 0.3    # non-KV runtime overhead not captured by weights
_MIN_CTX = 2048       # a model is only "usable" if it can hold at least this context


def detect_gpu() -> dict:
    """Return {'gpu': bool, 'vram_gb': float, 'name': str}. Falls back to CPU-only."""
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
                        "name": name.strip()}
        except (subprocess.SubprocessError, ValueError):
            pass
    return {"gpu": False, "vram_gb": 0.0, "name": "CPU only"}


def _kv_avail_gb(model: dict, vram_gb: float) -> float:
    """VRAM left for the KV cache after weights + overhead."""
    return vram_gb * _UTIL - model["weights_gb"] - _OVERHEAD_GB


def context_for(model: dict, vram_gb: float) -> int:
    """Largest context (multiple of 2048) that fits `vram_gb`, anchored to the value we
    validated at 6 GB and scaled by the KV memory available. Capped at the RoPE maximum."""
    base = _kv_avail_gb(model, 6.0)
    here = _kv_avail_gb(model, vram_gb)
    if base <= 0 or here <= 0:
        return 0
    ctx = int(model["ctx_6gb"] * here / base)
    return max(0, min((ctx // 2048) * 2048, model["rope_max"]))


def fits(model: dict, vram_gb: float) -> bool:
    return context_for(model, vram_gb) >= _MIN_CTX


def recommend_model(vram_gb: float | None = None, prefer: str = "quality") -> dict:
    """Pick the best model for the detected (or given) GPU.

    prefer: 'quality' (default) · 'context' (biggest window) · 'speed' (smallest model).
    Returns the model dict plus 'recommended_context' and 'reason'; or a CPU fallback note.
    """
    hw = detect_gpu()
    vram = hw["vram_gb"] if vram_gb is None else vram_gb
    usable = [m for m in MODELS if fits(m, vram)]
    if not usable:
        return {
            "name": None, "vram_gb": vram, "gpu": hw["name"],
            "reason": ("No AWQ model fits this GPU. vLLM needs a CUDA GPU — on CPU-only or "
                       "<2 GB VRAM, use the Ollama template (05-ollama-mcp-coder), which runs "
                       "GGUF models on CPU."),
        }
    if prefer == "context":
        best = max(usable, key=lambda m: context_for(m, vram))
    elif prefer == "speed":
        best = min(usable, key=lambda m: m["weights_gb"])
    else:  # quality
        best = max(usable, key=lambda m: m["quality"])
    ctx = context_for(best, vram)
    return {
        "name": best["name"], "vram_gb": vram, "gpu": hw["name"],
        "recommended_context": ctx, "quality": best["quality"], "rescue": best["rescue"],
        "thinking": best["thinking"], "note": best["note"],
        "reason": f"Highest '{prefer}' pick that fits {vram} GB with ≥{_MIN_CTX} ctx.",
    }
