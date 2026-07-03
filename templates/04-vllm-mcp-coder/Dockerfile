# Built with DockeDuck 🦆 — https://github.com/DimYun/DockDuck
# Copyright 2026 Dmitrii Iunovidov · Licensed under Apache-2.0 (see LICENSE, NOTICE)

FROM vllm/vllm-openai:latest

ARG USERNAME="appuser"
ARG UID="1000"
ARG GID="1000"

ENV WORK_DIR="/home/appuser/app" \
    HF_HOME="/home/appuser/.cache/huggingface"

# Add non-root user; groupadd may fail if GID already exists in the vllm base image
RUN groupadd --gid ${GID} ${USERNAME} 2>/dev/null || true && \
    useradd -l -m -s /bin/bash --uid ${UID} --gid ${GID} ${USERNAME} && \
    mkdir -p ${WORK_DIR} /home/appuser/.cache/huggingface && \
    chown -R ${USERNAME}:${GID} /home/appuser

RUN pip install --no-cache-dir \
    "mcp>=1.3.0" \
    "starlette>=0.41.0" \
    "uvicorn>=0.32.0" \
    "httpx>=0.27.0" \
    "pytest>=8.0.0"

WORKDIR ${WORK_DIR}
COPY --chown=${USERNAME}:${GID} src/ ./src/
COPY --chown=${USERNAME}:${GID} entrypoint.sh .
RUN chmod +x entrypoint.sh

USER ${USERNAME}

# 8000 = MCP SSE endpoint (external); 8001 = vLLM API (internal only)
EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]