# Ubuntu 22.04 with CUDA support
ARG CUDA_VERSION=12.8.1

FROM nvidia/cuda:${CUDA_VERSION}-cudnn-devel-ubuntu22.04 AS base

# Core arguments for idempotency and permissions
ARG PYTHON_VERSION="3.11.8"
ARG PKG_MANAGER="uv"
ARG USERNAME="appuser"
ARG UID="1000"
ARG GID="1000"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LC_ALL=en_US.UTF-8 \
    LANG=en_US.UTF-8 \
    LANGUAGE=en_US.UTF-8

# 1. Idempotent system installation
RUN apt-get update -y && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    curl git make build-essential libssl-dev zlib1g-dev \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 ninja-build \
    libbz2-dev libreadline-dev libsqlite3-dev wget llvm \
    libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    sudo acl rsync p7zip-full libglib2.0-0 libgl1-mesa-glx locales && \
    locale-gen en_US.UTF-8 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. Configure non-root user and /opt directory isolation
ENV OPT_DIR="/opt/dockduck"
ENV PYENV_ROOT="${OPT_DIR}/pyenv"
ENV CONDA_DIR="${OPT_DIR}/conda"
ENV VENV_DIR="${OPT_DIR}/python_venv/initial_venv"
ENV WORK_DIR="/home/${USERNAME}/app"

RUN groupadd --gid ${GID} ${USERNAME} && \
    useradd -l -m -s /bin/bash --uid ${UID} --gid ${GID} ${USERNAME}

# Create infrastructure directories in /opt and assign to non-root user
RUN mkdir -p ${VENV_DIR} ${CONDA_DIR} ${WORK_DIR} && \
    chown -R ${USERNAME}:${GID} /opt ${OPT_DIR} ${WORK_DIR} && \
    chmod -R g+rw /opt ${OPT_DIR} ${WORK_DIR}

USER ${USERNAME}
WORKDIR ${WORK_DIR}

# 3a. Slow layer: install Python runtime (conda / pyenv+venv / uv).
#     Cached as long as PYTHON_VERSION and PKG_MANAGER are unchanged.
#     requirements.txt is NOT copied yet so edits to it never bust this layer.
RUN if [ "$PKG_MANAGER" = "conda" ]; then \
        wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda.sh && \
        bash ~/miniconda.sh -b -u -p ${CONDA_DIR} && \
        rm ~/miniconda.sh && \
        ${CONDA_DIR}/bin/conda init bash && \
        ${CONDA_DIR}/bin/conda install -y python=${PYTHON_VERSION} pip; \
    else \
        curl https://pyenv.run | bash && \
        export PATH="${PYENV_ROOT}/bin:${PYENV_ROOT}/shims:${PATH}" && \
        pyenv install ${PYTHON_VERSION} && \
        pyenv global ${PYTHON_VERSION} && \
        python -m venv ${VENV_DIR} && \
        export PATH="${VENV_DIR}/bin:${PATH}" && \
        if [ "$PKG_MANAGER" = "uv" ]; then \
            curl -LsSf https://astral.sh/uv/install.sh | sh; \
        else \
            pip install --upgrade pip; \
        fi \
    fi

# 3b. Fast layer: install Python packages (~3 min).
#     Only reruns when requirements.txt actually changes.
COPY --chown=${USERNAME}:${GID} requirements.txt .
RUN if [ "$PKG_MANAGER" = "conda" ]; then \
        ${CONDA_DIR}/bin/pip install -r requirements.txt; \
    elif [ "$PKG_MANAGER" = "uv" ]; then \
        /home/${USERNAME}/.local/bin/uv pip install -r requirements.txt; \
    else \
        pip install -r requirements.txt; \
    fi

# Set path depending on package manager used
ENV PATH="/home/${USERNAME}/.local/bin:${CONDA_DIR}/bin:${VENV_DIR}/bin:${PYENV_ROOT}/bin:${PYENV_ROOT}/shims:${PATH}"
ENV VIRTUAL_ENV="${VENV_DIR}"

# ==========================================
# Build tour src environment and copy your files
# SAM2 as the example
# ==========================================
ENV FORCE_CUDA="1" \
    CUDA_HOME="/usr/local/cuda" \
    TORCH_CUDA_ARCH_LIST="7.0;7.5;8.0;8.6;8.9;9.0+PTX"

RUN if [ "$PKG_MANAGER" = "uv" ]; then \
        uv pip install wheel setuptools && \
#        uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 && \
#        uv pip install git+https://github.com/facebookresearch/sam2.git; \
    else \
        pip install wheel setuptools && \
#        pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 && \
#        pip install git+https://github.com/facebookresearch/sam2.git; \
    fi

COPY --chown=${USERNAME}:${GID} . ${WORK_DIR}/
CMD ["/bin/bash"]