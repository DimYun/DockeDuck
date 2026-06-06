# ── config ──────────────────────────────────────────────────────
IMAGE_NAME     ?= dockduck-base
TAG            ?= latest
PKG_MANAGER    ?= uv          # uv | pip | conda
PYTHON_VERSION ?= 3.11.8
CUDA_VERSION   ?= 12.8.1      # matches Dockerfile default; cuDNN 9, RTX 40-series ready

# Capture host user parameters automatically
UID := $(shell id -u)
GID := $(shell id -g)

# Load .env if present (HF_TOKEN, CLEARML_API_KEY, etc.)
-include .env
export

.PHONY: build run shell jupyter destroy help

build:  ## Build the Docker image (~15 min first time; ~3 min on requirements change)
	DOCKER_BUILDKIT=1 docker build \
		--build-arg UID=$(UID) \
		--build-arg GID=$(GID) \
		--build-arg PKG_MANAGER=$(PKG_MANAGER) \
		--build-arg PYTHON_VERSION=$(PYTHON_VERSION) \
		--build-arg CUDA_VERSION=$(CUDA_VERSION) \
		-t $(IMAGE_NAME):$(TAG) .

run:  ## Run src/main.py inside the container (GPU + project mounted)
	docker run --rm -it --gpus all \
		--env-file .env \
		-v $(PWD):/home/appuser/project \
		$(IMAGE_NAME):$(TAG) python src/main.py

jupyter:  ## Launch JupyterLab at http://localhost:8888 (GPU + project mounted)
	docker run --rm -it --gpus all \
		--network=host \
		--env-file .env \
		-v $(PWD):/home/appuser/app \
		$(IMAGE_NAME):$(TAG) \
		jupyter lab \
			--ip=0.0.0.0 --port=8888 --no-browser \
			--NotebookApp.token='' --NotebookApp.password='' \
			--notebook-dir=/home/appuser/app

shell:  ## Open a bash shell inside the container (GPU + project mounted)
	docker run --rm -it --gpus all \
		--env-file .env \
		-v $(PWD):/home/appuser/app \
		$(IMAGE_NAME):$(TAG) bash

destroy:  ## Remove the Docker image
	docker rmi $(IMAGE_NAME):$(TAG) || true

help:  ## List all available make targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | \
	    awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'