# =============================================================================
# FrostGate Core - Makefile
# production-grade / single source of truth / no drift
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

FG_ENV                ?= dev
FG_SERVICE            ?= frostgate-core
FG_AUTH_ENABLED       ?= 1
FG_API_KEY            ?= supersecret
FG_ENFORCEMENT_MODE   ?= observe
FG_DEV_EVENTS_ENABLED ?= 0

# State / artifacts
ARTIFACTS_DIR ?= artifacts
STATE_DIR     ?= state

# "Pinned" state dir for local runs (logs, pid, db)
FG_STATE_DIR   ?= $(CURDIR)/$(ARTIFACTS_DIR)
FG_SQLITE_PATH ?= $(FG_STATE_DIR)/frostgate.db

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
FG_BASE_URL="$(BASE_URL)" \
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
	  "  make fg-audit-make       Makefile target collision audit" \
	  "  make fg-contract         Contract linter" \
	  "  make fg-compile          py_compile core entrypoints" \
	  "  make fg-fast             audit + contract + compile + unit tests" \
	  "" \
	  "Local server:" \
	  "  make fg-up               start uvicorn (pid+log under artifacts/)" \
	  "  make fg-down             stop uvicorn" \
	  "  make fg-restart          restart uvicorn + wait for ready" \
	  "  make fg-ready            wait /health/ready" \
	  "  make fg-health           GET /health" \
	  "  make fg-logs N=200        tail uvicorn log" \
	  "  make fg-openapi-assert   assert key OpenAPI paths exist" \
	  "" \
	  "E2E:" \
	  "  make fg-e2e-http         start -> wait -> pytest -m e2e_http -> stop" \
	  "  make fg-e2e-local        fg-fast + fg-e2e-http" \
	  "" \
	  "Snapshot / No drift:" \
	  "  make fg-snapshot         update context snapshot (LATEST)" \
	  "  make fg-snapshot-all     full bundle snapshot" \
	  "  make fg-boot             fg-fast + fg-e2e-local + fg-snapshot" \
	  "" \
	  "Diagnostics:" \
	  "  make fg-doctor           environment + dependency + endpoint sanity" \
	  "" \
	  "CI / Guards:" \
	  "  make ci                  opinionated fast CI lane" \
	  "" \
	  "Legacy aliases:" \
	  "  make up-local/down-local/restart-local/logs-local/ready-local" \
	  "  make check/test" \
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
.PHONY: fg-audit-make fg-contract fg-compile
fg-audit-make:
	@./scripts/audit_make_targets.py

fg-contract:
	@./scripts/contract_lint.py

fg-compile:
	@$(PY) -m py_compile api/main.py api/feed.py api/dev_events.py

# =============================================================================
# Fast lane (no server)
# =============================================================================
.PHONY: fg-fast fg-check fg-test
fg-fast: fg-audit-make fg-contract fg-compile
	@$(PY) -m pytest -q

# aliases (muscle memory)
fg-check: fg-fast
fg-test: fg-fast

# =============================================================================
# Local server (canonical)
# =============================================================================
.PHONY: fg-up fg-down fg-restart fg-ready fg-health fg-logs fg-status

fg-up:
	mkdir -p "$(FG_STATE_DIR)" "$(STATE_DIR)"
	$(FG_RUN) ./scripts/uvicorn_local.sh start
	$(MAKE) -s fg-ready

fg-down:
	$(FG_RUN) ./scripts/uvicorn_local.sh stop || true

fg-restart:
	mkdir -p "$(FG_STATE_DIR)" "$(STATE_DIR)"
	$(FG_RUN) ./scripts/uvicorn_local.sh restart
	$(MAKE) -s fg-ready

fg-ready:
	@./scripts/uvicorn_local.sh check

fg-health:
	@curl -fsS "$(BASE_URL)/health" | $(PY) -m json.tool

fg-logs:
	@$(FG_RUN) ./scripts/uvicorn_local.sh logs $(or $(N),200)

fg-status:
	@set -euo pipefail; \
	echo "BASE_URL=$(BASE_URL)"; \
	echo "FG_ENV=$(FG_ENV)"; \
	echo "FG_AUTH_ENABLED=$(FG_AUTH_ENABLED)"; \
	echo "FG_ENFORCEMENT_MODE=$(FG_ENFORCEMENT_MODE)"; \
	echo "FG_STATE_DIR=$(FG_STATE_DIR)"; \
	echo "FG_SQLITE_PATH=$(FG_SQLITE_PATH)"; \
	test -f "$(FG_PIDFILE)" && echo "PID=$$(cat "$(FG_PIDFILE)")" || echo "PID=(none)"; \
	echo; \
	curl -fsS "$(BASE_URL)/health/live" 2>/dev/null || true; echo; \
	curl -fsS "$(BASE_URL)/health/ready" 2>/dev/null || true; echo

# =============================================================================
# OpenAPI reality checks (prevents Makefile lying)
# =============================================================================
.PHONY: fg-openapi-assert
fg-openapi-assert: fg-up
	@set -euo pipefail; \
	if ! command -v jq >/dev/null 2>&1; then \
	  echo "❌ jq is required for fg-openapi-assert"; \
	  exit 1; \
	fi; \
	curl -fsS "$(BASE_URL)/openapi.json" | jq -e '.paths | has("/health") and has("/health/ready") and has("/feed/live") and (has("/defend") or has("/v1/defend")) and has("/decisions") and has("/stats")' >/dev/null; \
	echo "✅ OpenAPI core endpoints present"; \
	$(MAKE) -s fg-down

# =============================================================================
# HTTP E2E (managed server lifecycle)
# =============================================================================
.PHONY: fg-e2e-http fg-e2e-local
fg-e2e-http: fg-up
	@FG_E2E_HTTP=1 FG_BASE_URL="$(BASE_URL)" FG_API_KEY="$(FG_API_KEY)" \
		$(PY) -m pytest -q -m e2e_http
	@$(MAKE) -s fg-down

fg-e2e-local: fg-fast fg-e2e-http

# =============================================================================
# Snapshot / No drift
# =============================================================================
.PHONY: fg-snapshot fg-snapshot-all fg-boot
fg-snapshot:
	@bash ./scripts/snapshot_context.sh

fg-snapshot-all:
	@bash ./scripts/snapshot_all.sh

fg-boot: fg-fast fg-e2e-local fg-snapshot
	@echo "✅ Boot complete. Snapshot updated."

# =============================================================================
# Diagnostics
# =============================================================================
.PHONY: fg-doctor
fg-doctor:
	@bash scripts/fg_doctor.sh

# =============================================================================
# CI / Guards (opinionated)
# =============================================================================
.PHONY: ci-tools guard-no-hardcoded-8000 guard-no-pytest-detection build-sidecar ci

ci-tools:
	@command -v rg >/dev/null || (echo "❌ rg missing" && exit 1)
	@command -v curl >/dev/null || (echo "❌ curl missing" && exit 1)
	@command -v sqlite3 >/dev/null || (echo "❌ sqlite3 missing" && exit 1)
	@echo "✅ CI tools present"

guard-no-hardcoded-8000:
	@rg -n "127\.0\.0\.1:8000|:8000\b" scripts api tests backend 2>/dev/null && \
	 (echo "❌ Hardcoded :8000 found. Use HOST/PORT/BASE_URL." && exit 1) || \
	 echo "✅ No hardcoded :8000 found"

guard-no-pytest-detection:
	@rg -n "_running_under_pytest|PYTEST_CURRENT_TEST|sys\.modules\['pytest'\]" api/main.py >/dev/null && \
	 (echo "❌ Pytest-detection found in api/main.py. Remove test hacks." && exit 1) || \
	 echo "✅ No pytest-detection in api/main.py"

build-sidecar:
	@cd supervisor-sidecar && go build ./...

# Default CI lane: fast, deterministic. HTTP e2e can be separate job/workflow.
ci: ci-tools guard-no-hardcoded-8000 guard-no-pytest-detection fg-fast build-sidecar

# =============================================================================
# Legacy aliases (keep docs/fingers intact)
# =============================================================================
.PHONY: up-local down-local restart-local logs-local ready-local health check test

up-local: fg-up
down-local: fg-down
restart-local: fg-restart
logs-local: fg-logs
ready-local: fg-ready
health: fg-health
check: fg-fast
test: fg-test
.PHONY: fg-ui-sse
fg-ui-sse:
	./scripts/apply_ui_sse_everything.sh || true
.PHONY: fg-ui-sse-smoke
fg-ui-sse-smoke:
	FG_NO_OPEN=1 ./scripts/apply_ui_sse_everything.sh >/dev/null 2>&1 || true
	./scripts/smoke_ui_sse.sh
