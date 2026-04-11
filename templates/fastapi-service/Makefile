.PHONY: build dev prod logs

build:
	DOCKER_BUILDKIT=1 docker build \
		--build-arg UID=$(shell id -u) \
		--build-arg GID=$(shell id -g) \
		-t $(IMAGE_NAME):$(TAG) .

dev:
	docker run --rm -it \
		-p 8000:8000 \
		-v $(PWD)/app:/home/appuser/app/app \
		$(IMAGE_NAME):$(TAG)

prod:
	docker run -d \
		--name $(IMAGE_NAME) \
		-p 8000:8000 \
		--restart unless-stopped \
		$(IMAGE_NAME):$(TAG)

logs:
	docker logs -f $(IMAGE_NAME)