PYTHON      ?= python
GO          ?= go

# Default: run unit tests only
.PHONY: test
test:
	PYTHONPATH=. $(PYTHON) -m pytest -q

# Build the Go supervisor binary (no install, just “does it compile”)
.PHONY: go-build
go-build:
	cd supervisor-sidecar && $(GO) build ./...

# CI entrypoint: what GitHub Actions should run
.PHONY: ci
ci: test go-build
