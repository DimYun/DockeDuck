# DockDuck

Fast non-root Docker dev environments for Python, ML, and API services.


## The core philosophy

Docker handles reproducibility, Makefile handles ergonomics, the non-root user handles security.

## Core features

* `scripts/init_project.sh` is the single most retweetable feature — one-liner project creation.
* Each template is self-contained so it can be copied directly without reading the whole repo.
* Switch managers with zero code changes: `make build PKG_MANAGER=pip` or `make build PKG_MANAGER=uv`.
* `dev` mounts the `app/` folder live for hot-reload - you edit code on host, Uvicorn restarts inside Docker.
* Non-Root Pattern (shared across all templates): 

```dockerfile
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


## Project structure

DockDuck/
├── README.md                   
├── LICENSE                     # MIT
├── CONTRIBUTING.md
├── .github/
│   ├── workflows/
│   │   ├── ci.yml              
│   │   └── release.yml         
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md
│       └── feature_request.md
│
├── templates/
│   ├── base-python/            # Template 1: plain Python + CUDA
│   │   ├── Dockerfile
│   │   ├── Makefile
│   │   ├── .env.example
│   │   └── requirements.txt
│   │
│   ├── pytorch-lightning/      # Template 2: training + ClearML
│   │   ├── Dockerfile
│   │   ├── Makefile
│   │   ├── .env.example
│   │   ├── requirements.txt
│   │   └── configs/
│   │       └── clearml.conf.example
│   │
│   └── fastapi-service/        # Template 3: FastAPI + Jinja2
│       ├── Dockerfile
│       ├── Makefile
│       ├── .env.example
│       ├── requirements.txt
│       └── app/
│           ├── main.py
│           └── templates/
│               └── index.html
│
├── scripts/
│   └── init_project.sh         # One-liner scaffold: ./init_project.sh pytorch-lightning myproject
│
├── docs/
│   ├── non-root-explained.md
│   ├── uv-vs-pip-benchmarks.md
│   └── cross-platform.md
│
└── Makefile                    # Root-level: list/build all templates
