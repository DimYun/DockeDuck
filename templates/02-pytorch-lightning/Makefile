IMAGE_NAME ?= dockduck-torch
DATA_DIR   ?= /tmp/data

UID := $(shell id -u)
GID := $(shell id -g)

build:
	DOCKER_BUILDKIT=1 docker build --build-arg UID=$(UID) --build-arg GID=$(GID) -t $(IMAGE_NAME) .

train:
	docker run --rm -it --gpus all \
		-v $(PWD):/home/appuser/project \
		-v $(DATA_DIR):/data \
		--env-file .env \
		$(IMAGE_NAME) python src/train.py

clearml-init:
	docker run --rm -it \
		-v $(PWD)/configs:/home/appuser/app/configs \
		$(IMAGE_NAME):$(TAG) clearml-init