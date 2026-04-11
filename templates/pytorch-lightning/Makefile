.PHONY: build train clearml-init

build:
	DOCKER_BUILDKIT=1 docker build \
		--build-arg UID=$(shell id -u) \
		--build-arg GID=$(shell id -g) \
		-t $(IMAGE_NAME):$(TAG) .

train:
	docker run --rm --gpus all \
		--env-file .env \
		-v $(PWD)/data:/home/appuser/app/data \
		-v $(PWD)/outputs:/home/appuser/app/outputs \
		$(IMAGE_NAME):$(TAG) python3 train.py configs/config.yaml

clearml-init:
	docker run --rm -it \
		-v $(PWD)/configs:/home/appuser/app/configs \
		$(IMAGE_NAME):$(TAG) clearml-init