# Ubuntu 22.04 with CUDA support
ARG CUDA_VERSION=12.2.0
ARG PYTHON_VERSION="3.11.8"
ARG PKG_MANAGER="uv"

FROM nvidia/cuda:${CUDA_VERSION}-base-ubuntu22.04 AS base

# Core arguments for idempotency and permissions
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
    libbz2-dev libreadline-dev libsqlite3-dev wget llvm \
    libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    sudo acl rsync p7zip-full libglib2.0-0 libgl1-mesa-glx && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. Configure non-root user and /opt directory isolation
ENV OPT_DIR="/opt/dockduck"
ENV PYENV_ROOT="${OPT_DIR}/pyenv"
ENV CONDA_DIR="${OPT_DIR}/conda"
ENV VENV_DIR="${OPT_DIR}/python_venv/initial_venv"
ENV WORK_DIR="/home/${USERNAME}/app"

RUN groupadd --gid ${GID} ${USERNAME} && \
    useradd -l -m -s /bin/bash --uid ${UID} --gid ${GID} ${USERNAME} && \
    echo "${USERNAME} ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/${USERNAME} && \
    chmod 0440 /etc/sudoers.d/${USERNAME}

# Create infrastructure directories in /opt and assign to non-root user
RUN mkdir -p ${PYENV_ROOT} ${VENV_DIR} ${WORK_DIR} && \
    chown -R ${USERNAME}:${GID} ${OPT_DIR} ${WORK_DIR} && \
    chmod -R g+rw ${OPT_DIR} ${WORK_DIR}

USER ${USERNAME}
WORKDIR ${WORK_DIR}

COPY --chown=${USERNAME}:${GID} requirements.txt .

RUN if [ "$PKG_MANAGER" = "conda" ]; then \
        wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda.sh && \
        bash ~/miniconda.sh -b -u -p ${CONDA_DIR} && \
        rm ~/miniconda.sh && \
        ${CONDA_DIR}/bin/conda init bash && \
        ${CONDA_DIR}/bin/conda install -y python=${PYTHON_VERSION} pip && \
        ${CONDA_DIR}/bin/pip install -r requirements.txt; \
    else \
        curl https://pyenv.run | bash && \
        export PATH="${PYENV_ROOT}/bin:${PYENV_ROOT}/shims:${PATH}" && \
        pyenv install ${PYTHON_VERSION} && \
        pyenv global ${PYTHON_VERSION} && \
        python -m venv ${VENV_DIR} && \
        export PATH="${VENV_DIR}/bin:${PATH}" && \
        if [ "$PKG_MANAGER" = "uv" ]; then \
            curl -LsSf https://astral.sh/uv/install.sh | sh && \
            /home/${USERNAME}/.cargo/bin/uv pip install --system -r requirements.txt; \
        else \
            pip install --upgrade pip && \
            pip install -r requirements.txt; \
        fi \
    fi

# Set path depending on package manager used
ENV PATH="${CONDA_DIR}/bin:${VENV_DIR}/bin:${PYENV_ROOT}/bin:${PYENV_ROOT}/shims:${PATH}"

# Copy the rest of the user project files
COPY --chown=${USERNAME}:${GID} . ${WORK_DIR}/

CMD ["/bin/bash"]