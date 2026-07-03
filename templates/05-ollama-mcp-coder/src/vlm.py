import os
import re

import httpx


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> reasoning blocks (Qwen3, Qwen3.5, and similar
    extended-thinking models emit these by default). Without this, the reasoning
    trace leaks into generated code and breaks syntax/execution."""
    return re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL).strip()


_FENCE_BLOCK_RE = re.compile(r'```[a-zA-Z0-9_+-]*\n(.*?)```', re.DOTALL)


def _strip_fences(text: str) -> str:
    """Extract file contents from a model response.

    Models add markdown fences despite being told not to. Chatty instruct models
    (e.g. Qwen2.5-Coder-3B-Instruct) also wrap the code in a ``` block with prose
    *around* it ("Certainly! Below is…```python…```…"). A boundary-only strip leaves
    that prose in place, so the code fails to parse. We therefore extract the fenced
    block when present (the longest, to skip tiny inline snippets), and only fall back
    to boundary-stripping when there is no fence at all.
    """
    text = _strip_thinking(text).strip()
    blocks = _FENCE_BLOCK_RE.findall(text)
    if blocks:
        return max(blocks, key=len).strip()
    text = re.sub(r'^```[a-zA-Z0-9_+-]*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return text.strip()


class CoderClient:
    def __init__(self) -> None:
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        # Native /api/chat — NOT the OpenAI-compatible /v1 endpoint. Only the native
        # endpoint honours `think: false`; /v1 silently ignores it (and /no_think),
        # so Qwen3 keeps thinking and burns ~10x the tokens. Verified empirically.
        self.api_url = f"{host.rstrip('/')}/api/chat"
        self.model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
        self.temperature = float(os.getenv("TEMPERATURE", "0.1"))
        self.max_tokens = int(os.getenv("MAX_TOKENS", "4096"))
        self.num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
        # Qwen3 / Qwen3.5 think by default. Disabled for code gen; set ENABLE_THINKING
        # to keep chain-of-thought (safe on non-thinking models — they ignore it).
        self.enable_thinking = os.getenv("ENABLE_THINKING", "false").lower() in ("1", "true", "yes")

    async def generate(self, prompt: str, system: str = "") -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(
                self.api_url,
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "think": self.enable_thinking,
                    "options": {
                        "temperature": self.temperature,
                        "num_predict": self.max_tokens,
                        "num_ctx": self.num_ctx,
                    },
                },
            )
            resp.raise_for_status()
            return _strip_fences(resp.json().get("message", {}).get("content", ""))
