<div align="center">
  <img src="docs/logo.png" alt="DockDuck Logo" width="200" style="border-radius: 20px;"/>
  <h1>🦆 DockDuck</h1>

  <p><b>Fast, secure, and non-root Docker development environments for Python, ML, and APIs.</b></p>

  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://github.com/dimyun/dockduck/pulls"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome"></a>
</div>

---

# 🦆 DockeDuck

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://dimyun.space/)


**Fast, secure, and non-root Docker development environments for Python, Machine Learning, and API services.**

Stop fighting permission errors and messy host environments. 
DockeDuck provides battle-tested, copy-pasteable Docker templates that just work.


## 🧠 The Core Philosophy

1. **Docker** handles environment reproducibility.
2. **Makefile** handles developer ergonomics and command simplicity.
3. **The Non-Root User** handles host system security and file permission sanity.


## ✨ Key Features

* **The One-Liner Scaffold:** `scripts/init_project.sh` is our standout feature. You don't need to fork or clone the whole repo for your daily work. Just run one script to generate a fully isolated project directory anywhere on your machine.
* **Self-Contained Templates:** Every template is designed to work independently. Copy a template folder directly into your own repository, and you are ready to go.
* **Zero-Code Package Manager Switch:** Swap between `conda`, `pip` and the blazingly fast `uv` dynamically at build time:
  * `make build PKG_MANAGER=conda`
  * `make build PKG_MANAGER=pip`
  * `make build PKG_MANAGER=uv`
* **Live Hot-Reloading:** The default `dev` commands automatically mount your local `app/` folder. Edit code in your IDE on the host machine, and tools like Uvicorn instantly restart inside the isolated Docker container.
* **The Universal Non-Root Pattern:** Built into every template to solve the classic "files created inside Docker are owned by root on the host" nightmare:

```dockerfile
# Standardized across all DockDuck templates
ARG UID=1000
ARG GID=1000
ARG USERNAME=appuser

RUN groupadd --gid ${GID} ${USERNAME} && \
    useradd --uid ${UID} --gid ${GID} --create-home --shell /bin/bash ${USERNAME}

USER ${USERNAME}
WORKDIR /home/${USERNAME}/app
```
Note:

* Pass your real UID at build time: `docker build --build-arg UID=$(id -u) --build-arg GID=$(id -g)` to solves the classic "files created inside Docker are owned by root on the host" problem.
* ClearML credentials go in `.env`.
* You will need `docker-buildx`.


## 🚀 Quick Start & Examples

### 1. Scaffold a New Project (Recommended)
Use our initialization script to instantly create a new project based on any template.

```bash
./scripts/init_project.sh pytorch-lightning /path/to/my_new_project
cd /path/to/my_new_project
make build
```

> Important: Don't forget to make the script executable by running this in your terminal:
`chmod +x scripts/init_project.sh`

### 2. Base CUDA: Running Isolated JupyterLab
Need a clean Jupyter environment with GPU access? 
Start a session completely isolated in Docker while safely saving your
notebooks locally.

```bash
cd templates/base-python
make build
docker run --rm -it --gpus all \
    -p 8888:8888 \
    -v $(pwd):/home/appuser/app \
    dockduck-base:latest \
    jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --NotebookApp.token='' --NotebookApp.password=''
```

Access JupyterLab at http://localhost:8888

### 3. ML Ops: Training a PyTorch Lightning Model
A ready-to-go environment for deep learning. Put your ClearML or 
W&B API keys in the .env file, and you are ready to train.

```bash
cd templates/pytorch-lightning
make build
make train
```

### 4. Web Services: FastAPI with Hot Reload
A modern, asynchronous web API environment equipped with Jinja2 rendering and live reloading.

```bash
cd templates/fastapi-service
make build
make start
```


## 📂 Project Structure & Architecture

DockeDuck is organized to keep internal documentation, templates,
and scaffolding scripts strictly separated.

```plaintext
DockeDuck/
├── README.md                   # Project overview and quick start
├── LICENSE                     # MIT License
├── CONTRIBUTING.md             # Guidelines for adding new templates
├── .github/                    # CI/CD and Community Health
│   ├── workflows/
│   │   ├── ci.yml              # Automated build tests for templates
│   │   └── release.yml         # Automated release tagging
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md
│       └── feature_request.md
│
├── templates/                  # The core environments (Self-contained)
│   │
│   ├── base-python/            # Template 1: Plain Python + NVIDIA CUDA
│   │   ├── Dockerfile          # Heavy GPU-enabled base image
│   │   ├── Makefile            # Build and interactive shell wrappers
│   │   ├── .env.example        # Environment variable definitions
│   │   └── requirements.txt    # Base dependencies
│   │
│   ├── pytorch-lightning/      # Template 2: ML Training + ClearML tracking
│   │   ├── Dockerfile
│   │   ├── Makefile            # Includes `make train` hooks
│   │   ├── .env.example        # Stores ClearML credentials safely
│   │   ├── requirements.txt
│   │   └── configs/
│   │       └── clearml.conf.example
│   │
│   └── fastapi-service/        # Template 3: Web Backend (FastAPI + Jinja2)
│       ├── Dockerfile          # Lightweight API base image
│       ├── Makefile            # Includes `make start` for hot-reloading
│       ├── .env.example
│       ├── requirements.txt
│       └── app/                # Mounted local directory
│           ├── main.py         # Uvicorn entrypoint
│           └── templates/
│               └── index.html
│
├── scripts/
│   └── init_project.sh         # The main CLI tool: scaffolds templates to target dirs
│
├── docs/                       # Deep-dive documentation
│   ├── non-root-explained.md   # Why and how our permission architecture works
│   ├── uv-vs-pip-benchmarks.md # Build speed comparisons
│   └── cross-platform.md       # Windows (WSL2) and macOS specific quirks
│
└── Makefile                    # Root-level Make: easily list or build all templates at once
```
