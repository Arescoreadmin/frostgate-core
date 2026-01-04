# =============================================================================
# FrostGate Core - Makefile (production grade / single source of truth / no drift)
# =============================================================================

SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -euo pipefail -c
.DELETE_ON_ERROR:

# -----------------------------------------------------------------------------
# Repo + Python
# -----------------------------------------------------------------------------
VENV   ?= .venv
PY     ?= $(VENV)/bin/python
PIP    ?= $(VENV)/bin/pip
export PYTHONPATH := .

# -----------------------------------------------------------------------------
# Runtime defaults (single source of truth)
# -----------------------------------------------------------------------------
HOST     ?= 127.0.0.1
PORT     ?= 8000
BASE_URL ?= http://$(HOST):$(PORT)

FG_ENV              ?= dev
FG_SERVICE          ?= frostgate-core
FG_AUTH_ENABLED     ?= 1
FG_API_KEY          ?= supersecret
FG_ENFORCEMENT_MODE ?= observe
FG_DEV_EVENTS_ENABLED ?= 1

# State / artifacts
ARTIFACTS_DIR ?= artifacts
STATE_DIR     ?= state

# "Pinned" state dir for local runs (logs, pid, db)
FG_STATE_DIR   ?= $(CURDIR)/$(ARTIFACTS_DIR)
FG_SQLITE_PATH ?= $(FG_STATE_DIR)/frostgate.db

# Evidence/demo
EVIDENCE_DIR ?= $(ARTIFACTS_DIR)/evidence
SCENARIO     ?= spike

# Legacy mirror (some scripts/tests may still read API_KEY)
export API_KEY := $(FG_API_KEY)

# -----------------------------------------------------------------------------
# Centralized env injector (single source of truth)
# -----------------------------------------------------------------------------
define FG_RUN
FG_ENV="$(FG_ENV)" \
FG_SERVICE="$(FG_SERVICE)" \
FG_AUTH_ENABLED="$(FG_AUTH_ENABLED)" \
FG_API_KEY="$(FG_API_KEY)" \
FG_ENFORCEMENT_MODE="$(FG_ENFORCEMENT_MODE)" \
FG_STATE_DIR="$(FG_STATE_DIR)" \
FG_SQLITE_PATH="$(FG_SQLITE_PATH)" \
FG_DEV_EVENTS_ENABLED="$(FG_DEV_EVENTS_ENABLED)" \
BASE_URL="$(BASE_URL)" \
HOST="$(HOST)" \
PORT="$(PORT)" \
API_KEY="$(FG_API_KEY)"
endef

# -----------------------------------------------------------------------------
# Uvicorn wrapper integration (pid-safe, log-safe)
# scripts/uvicorn_local.sh expects these
# -----------------------------------------------------------------------------
export FG_HOST    := $(HOST)
export FG_PORT    := $(PORT)
export FG_PIDFILE := $(FG_STATE_DIR)/uvicorn.local.pid
export FG_LOGFILE := $(FG_STATE_DIR)/uvicorn.local.log
export FG_APP     := api.main:app
export FG_PY      := $(PY)

# =============================================================================
# Help
# =============================================================================
.PHONY: help
help:
	@printf "%s\n" \
	  "FrostGate Core - Targets" \
	  "" \
	  "Setup:" \
	  "  make venv" \
	  "" \
	  "Fast gates (no server):" \
	  "  make fg-audit-make      Makefile target collision audit" \
	  "  make fg-contract        Contract linter" \
	  "  make fg-fast            audit + contract + compile + unit" \
	  "  make fg-check           alias for fg-fast" \
	  "" \
	  "Local server:" \
	  "  make fg-dev-up          start uvicorn (pid+log under artifacts/)" \
	  "  make fg-dev-down        stop uvicorn" \
	  "  make fg-dev-restart     restart uvicorn + wait for ready" \
	  "  make fg-ready           wait /health/ready" \
	  "  make fg-health          GET /health" \
	  "  make fg-seed            POST /dev/seed (deterministic; requires dev enabled)" \
	  "" \
	  "E2E:" \
	  "  make fg-e2e-local       start -> wait -> seed -> pytest -m e2e_http -> stop" \
	  "  make fg-e2e-http        run http tests against an already running server" \
	  "" \
	  "CI / Guards:" \
	  "  make ci                 fast lane + sidecar build + guardrails" \
	  "" \
	  "Legacy aliases:" \
	  "  make up-local / down-local / restart-local / ready-local / seed-dev / check / test" \
	  ""

# =============================================================================
# Setup
# =============================================================================
.PHONY: venv
venv:
	test -d "$(VENV)" || python -m venv "$(VENV)"
	"$(PIP)" install --upgrade pip
	"$(PIP)" install -r requirements.txt -r requirements-dev.txt

# =============================================================================
# Guardrails / audits
# =============================================================================
.PHONY: fg-audit-make fg-contract
fg-audit-make:
	@./scripts/audit_make_targets.py

fg-contract:
	@./scripts/contract_lint.py

# =============================================================================
# Local server (pid-safe wrapper)
# =============================================================================
.PHONY: fg-dev-up fg-dev-down fg-dev-restart fg-logs fg-ready fg-health fg-seed fg-status

fg-dev-up:
	mkdir -p "$(FG_STATE_DIR)" "$(STATE_DIR)"
	$(FG_RUN) ./scripts/uvicorn_local.sh start
	$(MAKE) -s fg-ready

fg-dev-down:
	$(FG_RUN) ./scripts/uvicorn_local.sh stop || true

fg-dev-restart:
	mkdir -p "$(FG_STATE_DIR)" "$(STATE_DIR)"
	$(FG_RUN) ./scripts/uvicorn_local.sh restart
	$(MAKE) -s fg-ready

fg-logs:
	$(FG_RUN) ./scripts/uvicorn_local.sh logs $(or $(N),200)

fg-ready:
	@set -euo pipefail; \
	./scripts/uvicorn_local.sh check

fg-health:
	curl -fsS "$(BASE_URL)/health" | $(PY) -m json.tool

fg-seed:
	@set -euo pipefail; \
	curl -fsS -X POST -H "x-api-key: $(FG_API_KEY)" "$(BASE_URL)/dev/seed" | $(PY) -m json.tool >/dev/null; \
	echo "✅ seeded"

fg-status:
	@set -euo pipefail; \
	echo "BASE_URL=$(BASE_URL)"; \
	echo "FG_AUTH_ENABLED=$(FG_AUTH_ENABLED)"; \
	echo "FG_DEV_EVENTS_ENABLED=$(FG_DEV_EVENTS_ENABLED)"; \
	echo "FG_STATE_DIR=$(FG_STATE_DIR)"; \
	echo "FG_SQLITE_PATH=$(FG_SQLITE_PATH)"; \
	test -f "$(FG_PIDFILE)" && echo "PID=$$(cat "$(FG_PIDFILE)")" || echo "PID=(none)"; \
	echo; \
	curl -fsS "$(BASE_URL)/health/live" 2>/dev/null || true; echo; \
	curl -fsS "$(BASE_URL)/health/ready" 2>/dev/null || true; echo

# =============================================================================
# Tests (tiered)
# =============================================================================
.PHONY: fg-fast fg-check fg-test fg-e2e-local fg-e2e-http fg-compile

fg-compile:
	$(PY) -m py_compile api/main.py api/feed.py api/dev_events.py

# Fast lane: no live server. Deterministic. Runs what CI should run by default.
fg-fast: fg-audit-make fg-contract fg-compile
	$(PY) -m pytest -q

# alias (keeps existing muscle memory)
fg-check: fg-fast
fg-test: fg-fast

# HTTP E2E: manage server lifecycle locally, then run only e2e_http tests
.PHONY: fg-e2e-local
fg-e2e-local: fg-fast
	@bash -lc 'set -euo pipefail; \
	mkdir -p "$(PWD)/artifacts" "$(PWD)/state"; \
	export FG_ENV=dev; \
	export FG_SERVICE=frostgate-core; \
	export FG_AUTH_ENABLED=1; \
	export FG_API_KEY=supersecret; \
	export FG_ENFORCEMENT_MODE=observe; \
	export FG_STATE_DIR="$(PWD)/artifacts"; \
	export FG_SQLITE_PATH="$(PWD)/artifacts/frostgate.e2e.db"; \
	export FG_DEV_EVENTS_ENABLED=1; \
	export FG_BASE_URL=http://127.0.0.1:8000; \
	export FG_HOST=127.0.0.1; \
	export FG_PORT=8000; \
	export BASE_URL=http://127.0.0.1:8000; \
	export API_KEY=supersecret; \
	export FG_STRICT_START=0; \
	export FG_RESTART_IF_RUNNING=1; \
	export FG_READY_REQUIRED=$${FG_READY_REQUIRED:-1}; \
	trap "./scripts/uvicorn_local.sh stop >/dev/null 2>&1 || true" EXIT; \
	./scripts/uvicorn_local.sh start; \
	./scripts/uvicorn_local.sh openapi; \
	$(MAKE) -s fg-ready; \
	$(MAKE) -s fg-seed; \
	$(PY) -m pytest -q -m e2e_http; \
	'

# Explicitly run http e2e against an already-running server (no start/stop).
fg-e2e-http:
	@$(PY) -m pytest -q -m e2e_http


# =============================================================================
# CI / Guards (keep these opinionated)
# =============================================================================
.PHONY: ci-tools guard-no-8000 guard-no-pytest-detection build-sidecar ci

ci-tools:
	@command -v rg >/dev/null || (echo "❌ rg missing" && exit 1)
	@command -v curl >/dev/null || (echo "❌ curl missing" && exit 1)
	@command -v sqlite3 >/dev/null || (echo "❌ sqlite3 missing" && exit 1)
	@echo "✅ CI tools present"

guard-no-8000:
	@rg -n "127\.0\.0\.1:8000|:8000\b" scripts api tests backend 2>/dev/null && \
	 (echo "❌ Hardcoded :8000 found. Use HOST/PORT/BASE_URL." && exit 1) || \
	 echo "✅ No hardcoded :8000 found"

guard-no-pytest-detection:
	@rg -n "_running_under_pytest|PYTEST_CURRENT_TEST|sys\.modules\['pytest'\]" api/main.py >/dev/null && \
	 (echo "❌ Pytest-detection found in api/main.py. Remove test hacks." && exit 1) || \
	 echo "✅ No pytest-detection in api/main.py"

build-sidecar:
	@cd supervisor-sidecar && go build ./...

# CI runs fast lane by default. HTTP e2e should be a separate workflow/job.
ci: ci-tools guard-no-8000 guard-no-pytest-detection fg-fast build-sidecar

# =============================================================================
# Legacy aliases (keep your docs and fingers intact)
# =============================================================================
.PHONY: up-local down-local restart-local logs-local ready-local seed-dev health check test

up-local: fg-dev-up
down-local: fg-dev-down
restart-local: fg-dev-restart
logs-local: fg-logs
ready-local: fg-ready
seed-dev: fg-seed
health: fg-health
check: fg-check
test: fg-test
