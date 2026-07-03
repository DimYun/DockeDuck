"""Unit tests for the hardware-advisor modules behind the recommend_* MCP tools.
Pure logic — no GPU required (VRAM is passed in explicitly)."""
from conftest import load_module

hw_vllm = load_module("templates/04-vllm-mcp-coder/src/hardware.py", "hw_vllm")
hw_ollama = load_module("templates/05-ollama-mcp-coder/src/hardware.py", "hw_ollama")


def test_context_matches_measured_6gb_vllm():
    ctx = {m["name"]: hw_vllm.context_for(m, 6.0) for m in hw_vllm.MODELS}
    assert ctx["Qwen/Qwen3-4B-AWQ"] == 8192              # 8 KV heads → capped low
    assert ctx["Qwen/Qwen2.5-Coder-3B-Instruct-AWQ"] == 24576
    assert ctx["Qwen/Qwen2.5-Coder-1.5B-Instruct-AWQ"] == 32768


def test_context_matches_measured_6gb_ollama():
    ctx = {m["tag"]: hw_ollama.context_for(m, 6.0) for m in hw_ollama.MODELS}
    assert ctx["qwen2.5-coder:3b"] == 16384
    assert ctx["qwen3.5:4b"] == 12288


def test_context_monotonic_in_vram():
    m = hw_vllm.MODELS[0]
    assert hw_vllm.context_for(m, 4.0) <= hw_vllm.context_for(m, 6.0) <= hw_vllm.context_for(m, 12.0)


def test_context_capped_at_rope_max():
    for m in hw_vllm.MODELS:
        assert hw_vllm.context_for(m, 80.0) <= m["rope_max"]


def test_recommend_prefers_correctly_vllm():
    assert hw_vllm.recommend_model(6.0, "quality")["name"] == "Qwen/Qwen3-4B-AWQ"
    # biggest context on 6 GB is the 2-KV-head coder, not the 4B
    assert "Coder" in hw_vllm.recommend_model(6.0, "context")["name"]
    assert "1.5B" in hw_vllm.recommend_model(6.0, "speed")["name"]


def test_recommend_downsizes_on_small_gpu():
    # a 2 GB GPU can't hold the 4B with a usable context → fall back to a coder model
    rec = hw_vllm.recommend_model(2.0, "quality")
    assert rec["name"] is None or "Coder" in rec["name"]


def test_vllm_cpu_points_to_ollama():
    rec = hw_vllm.recommend_model(0.0, "quality")
    assert rec["name"] is None
    assert "ollama" in rec["reason"].lower()


def test_ollama_runs_on_cpu():
    # Ollama runs on CPU: at least one model must fit using RAM when there is no GPU.
    cpu = {"gpu": False, "vram_gb": 0.0, "ram_gb": 16.0, "name": "CPU only"}
    assert any(hw_ollama.fits(m, cpu) for m in hw_ollama.MODELS)


def test_detect_returns_expected_keys():
    d = hw_vllm.detect_gpu()
    assert set(d) >= {"gpu", "vram_gb", "name"}
    o = hw_ollama.detect_hardware()
    assert set(o) >= {"gpu", "vram_gb", "ram_gb", "name"}
