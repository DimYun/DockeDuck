# ── config ──────────────────────────────────────────────────────
IMAGE_NAME     ?= dockduck-base
TAG            ?= latest
PKG_MANAGER    ?= uv          # uv | pip | conda
PYTHON_VERSION ?= 3.11.8
CUDA_VERSION   ?= 12.2.0

# Capture host user parameters automatically
UID := $(shell id -u)
GID := $(shell id -g)

.PHONY: build run shell clean

build:
	DOCKER_BUILDKIT=1 docker build \
		--build-arg UID=$(UID) \
		--build-arg GID=$(GID) \
		--build-arg PKG_MANAGER=$(PKG_MANAGER) \
		--build-arg PYTHON_VERSION=$(PYTHON_VERSION) \
		--build-arg CUDA_VERSION=$(CUDA_VERSION) \
		-t $(IMAGE_NAME):$(TAG) .

run:
	docker run --rm -it --gpus all \
		-p 8888:8888 \
		-v $(PWD):/home/appuser/project \
		$(IMAGE_NAME):$(TAG) python src/main.py

shell:
	docker run --rm -it --gpus all \
		-v $(PWD):/home/appuser/app \
		$(IMAGE_NAME):$(TAG) bash

destroy:
	docker rmi $(IMAGE_NAME):$(TAG) || true