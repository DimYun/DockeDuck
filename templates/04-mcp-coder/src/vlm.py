import os
import re

import httpx


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> reasoning blocks (Qwen3, Qwen3.5, and similar
    extended-thinking models emit these by default). Without this, the reasoning
    trace leaks into generated code and breaks syntax/execution."""
    return re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL).strip()


def _strip_fences(text: str) -> str:
    """Remove markdown code fences that models add despite being told not to."""
    text = _strip_thinking(text)
    text = text.strip()
    text = re.sub(r'^```[a-zA-Z]*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return text.strip()


class CoderClient:
    def __init__(self) -> None:
        port = os.getenv("VLLM_PORT", "8001")
        self.base_url = f"http://127.0.0.1:{port}/v1"
        self.model = os.getenv("VLM_MODEL", "")
        self.temperature = float(os.getenv("TEMPERATURE", "0.1"))
        self.max_tokens = int(os.getenv("MAX_TOKENS", "4096"))
        # Qwen3 / Qwen3.5 think by default, burning thousands of tokens before any
        # code. For deterministic code generation we disable it (the /no_think soft
        # switch is honoured by Qwen3 models and ignored harmlessly by others).
        self.enable_thinking = os.getenv("ENABLE_THINKING", "false").lower() in ("1", "true", "yes")

    async def generate(self, prompt: str, system: str = "") -> str:
        if not self.enable_thinking:
            system = (system + " /no_think").strip()

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                },
            )
            resp.raise_for_status()
            return _strip_fences(resp.json()["choices"][0]["message"]["content"])
