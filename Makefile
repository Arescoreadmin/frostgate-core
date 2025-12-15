# Makefile

SHELL := /bin/bash

# Image coordinates
REGISTRY        ?= ghcr.io
IMAGE_OWNER     ?= your-org-or-user
CORE_IMAGE_NAME ?= frostgate-core
SIDE_IMAGE_NAME ?= frostgate-supervisor-sidecar

CORE_IMAGE      := $(REGISTRY)/$(IMAGE_OWNER)/$(CORE_IMAGE_NAME)
SIDE_IMAGE      := $(REGISTRY)/$(IMAGE_OWNER)/$(SIDE_IMAGE_NAME)

VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)

VENV    ?= .venv
PYTHON  ?= $(VENV)/bin/python
PIP     ?= $(VENV)/bin/pip

# ----------------------------
# Local Dev
# ----------------------------

.PHONY: venv
venv:
	test -d $(VENV) || python -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt

.PHONY: test
test:
	PYTHONPATH=. $(PYTHON) -m pytest -q

.PHONY: build
build:
	cd supervisor-sidecar && go build ./...

.PHONY: ci
ci: test build

# ----------------------------
# Docker
# ----------------------------

.PHONY: docker-build
docker-build:
	docker build \
		-t $(CORE_IMAGE):$(VERSION) \
		-t $(CORE_IMAGE):latest \
		.
	docker build \
		-t $(SIDE_IMAGE):$(VERSION) \
		-t $(SIDE_IMAGE):latest \
		supervisor-sidecar

.PHONY: docker-push
docker-push:
	docker push $(CORE_IMAGE):$(VERSION)
	docker push $(CORE_IMAGE):latest
	docker push $(SIDE_IMAGE):$(VERSION)
	docker push $(SIDE_IMAGE):latest

.PHONY: docker-release
docker-release: docker-build docker-push

# ----------------------------
# Utilities
# ----------------------------

.PHONY: print-version
print-version:
	@echo $(VERSION)

.PHONY: print-images
print-images:
	@echo "Core: $(CORE_IMAGE):$(VERSION)"
	@echo "Sidecar: $(SIDE_IMAGE):$(VERSION)"

# ----------------------------
# Tenant Tools
# ----------------------------

.PHONY: tenant-add
tenant-add:
	@test -n "$(TENANT_ID)" || (echo "TENANT_ID is required" && exit 1)
	PYTHONPATH=. $(PYTHON) -m tools.tenants add $(TENANT_ID)

.PHONY: tenant-list
tenant-list:
	PYTHONPATH=. $(PYTHON) -m tools.tenants list

# ----------------------------
# Local docker convenience
# ----------------------------

.PHONY: docker-build-local docker-run docker-shell

docker-build-local:
	docker build -t frostgate-core:local .

docker-run:
	docker run --rm -p 8080:8080 \
	  -e FROSTGATE_ENV=dev \
	  -e FROSTGATE_ENFORCEMENT_MODE=observe \
	  -e FROSTGATE_LOG_LEVEL=DEBUG \
	  frostgate-core:local

docker-shell:
	docker run --rm -it frostgate-core:local /bin/bash

.PHONY: test build-dev build-prod deploy-dev deploy-prod

test:
	ENVIRONMENT=dev scripts/test.sh

build-dev:
	ENVIRONMENT=dev PUSH_IMAGE=0 scripts/build.sh

build-prod:
	ENVIRONMENT=prod scripts/build.sh

deploy-dev:
	ENVIRONMENT=dev scripts/deploy_dev.sh

deploy-prod:
	ENVIRONMENT=prod VERSION=$(VERSION) scripts/deploy_prod.sh

