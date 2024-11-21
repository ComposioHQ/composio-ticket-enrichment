IMAGE_NAME = composio/ticket-enrichment
TAG = latest

.PHONY: all clean build run

all: clean build run

clean:
	-docker rm -f $$(docker ps -a -q --filter ancestor=$(IMAGE_NAME):$(TAG))
	-docker rmi -f $(IMAGE_NAME):$(TAG)

build:
	docker build -f Dockerfile -t $(IMAGE_NAME):$(TAG) .

delete:
	docker rm -f $$(docker ps -a -q --filter ancestor=$(IMAGE_NAME):$(TAG))

run:
	docker run -d --name composio-ticket-enrichment --restart unless-stopped --env-file .env $(IMAGE_NAME):$(TAG)
