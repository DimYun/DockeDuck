# ── config ──────────────────────────────────────────────────────
IMAGE_NAME   ?= base-python
TAG          ?= latest
PKG_MANAGER  ?= uv          # uv | pip | conda
CUDA_VERSION ?= 12.1.0
UID          := $(shell id -u)
GID          := $(shell id -g)

# ── targets ─────────────────────────────────────────────────────
.PHONY: build run shell clean

build:
	DOCKER_BUILDKIT=1 docker build \
		--build-arg UID=$(UID) \
		--build-arg GID=$(GID) \
		--build-arg PKG_MANAGER=$(PKG_MANAGER) \
		--build-arg CUDA_VERSION=$(CUDA_VERSION) \
		-t $(IMAGE_NAME):$(TAG) .

run:
	docker run --rm --gpus all \
		-v $(PWD):/home/appuser/app \
		$(IMAGE_NAME):$(TAG)

shell:
	docker run --rm -it --gpus all \
		-v $(PWD):/home/appuser/app \
		$(IMAGE_NAME):$(TAG) bash

clean:
	docker rmi $(IMAGE_NAME):$(TAG)