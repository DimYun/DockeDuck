IMAGE_NAME ?= dockduck-api

UID := $(shell id -u)
GID := $(shell id -g)

build:
	DOCKER_BUILDKIT=1 docker build --build-arg UID=$(UID) --build-arg GID=$(GID) -t $(IMAGE_NAME) .

dev:
	docker run --rm -it \
		-p 8000:8000 \
		-v $(PWD):/home/appuser/project \
		$(IMAGE_NAME) uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

