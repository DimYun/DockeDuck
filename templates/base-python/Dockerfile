ARG CUDA_VERSION=12.1.0
ARG PYTHON_VERSION=3.11
FROM nvidia/cuda:${CUDA_VERSION}-cudnn8-runtime-ubuntu22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python${PYTHON_VERSION} python3-pip python3-venv curl git \
    && rm -rf /var/lib/apt/lists/*

# --- non-root user ---
ARG UID=1000
ARG GID=1000
RUN groupadd --gid ${GID} appuser && \
    useradd --uid ${UID} --gid ${GID} --create-home appuser

USER appuser
WORKDIR /home/appuser/app

# --- package manager stage (venv + uv) ---
ENV VIRTUAL_ENV=/home/appuser/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# uv install (non-root, into ~/.local)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/home/appuser/.local/bin:$PATH"

COPY --chown=appuser:appuser requirements.txt .

# switch via build arg: pip | uv | conda
ARG PKG_MANAGER=uv
RUN if [ "$PKG_MANAGER" = "uv" ]; then \
        uv venv ${VIRTUAL_ENV} && uv pip install -r requirements.txt; \
    elif [ "$PKG_MANAGER" = "conda" ]; then \
        conda env create -f environment.yml; \
    else \
        python3 -m venv ${VIRTUAL_ENV} && pip install --upgrade pip && \
        pip install -r requirements.txt; \
    fi

CMD ["python3"]