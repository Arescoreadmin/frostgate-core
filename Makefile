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
FG_UI_TOKEN_GET_ENABLED ?= 1

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
FG_UI_TOKEN_GET_ENABLED="$(FG_UI_TOKEN_GET_ENABLED)" \
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
	  "UI / SSE:" \
	  "  make fg-ui-sse           apply SSE UI wiring + restart" \
	  "  make fg-ui-sse-smoke     strict smoke for cookie + /feed/live + SSE" \
	  "" \
	  "Diagnostics:" \
	  "  make fg-doctor           compile + restart + UI/SSE smoke" \
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
	@$(PY) -m py_compile api/main.py api/feed.py api/ui.py api/dev_events.py api/auth_scopes.py

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

# IMPORTANT: keep env consistent here too (no drift)
fg-ready:
	@$(FG_RUN) ./scripts/uvicorn_local.sh check

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
# NOTE: trap ensures fg-down runs even on failure
# =============================================================================
.PHONY: fg-openapi-assert
fg-openapi-assert: fg-up
	@set -euo pipefail; \
	trap '$(MAKE) -s fg-down >/dev/null 2>&1 || true' EXIT; \
	if ! command -v jq >/dev/null 2>&1; then \
	  echo "❌ jq is required for fg-openapi-assert"; \
	  exit 1; \
	fi; \
	curl -fsS "$(BASE_URL)/openapi.json" | jq -e '.paths | has("/health") and has("/health/ready") and has("/feed/live") and (has("/defend") or has("/v1/defend")) and has("/decisions") and has("/stats")' >/dev/null; \
	echo "✅ OpenAPI core endpoints present"

# =============================================================================
# HTTP E2E (managed server lifecycle)
# NOTE: trap ensures fg-down runs even on pytest failure
# =============================================================================
.PHONY: fg-e2e-http fg-e2e-local
fg-e2e-http: fg-up
	@set -euo pipefail; \
	trap '$(MAKE) -s fg-down >/dev/null 2>&1 || true' EXIT; \
	FG_E2E_HTTP=1 FG_BASE_URL="$(BASE_URL)" FG_API_KEY="$(FG_API_KEY)" \
		$(PY) -m pytest -q -m e2e_http

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
# UI / SSE helpers
# =============================================================================
.PHONY: fg-ui-sse fg-ui-sse-smoke
fg-ui-sse:
	@./scripts/apply_ui_sse_everything.sh || true

fg-ui-sse-smoke:
	@FG_NO_OPEN=1 ./scripts/apply_ui_sse_everything.sh >/dev/null 2>&1 || true
	@./scripts/smoke_ui_sse.sh

# =============================================================================
# Diagnostics
# =============================================================================
.PHONY: fg-doctor
fg-doctor:
	@bash scripts/fg_doctor.sh

.PHONY: fg-smoke-auth
fg-smoke-auth:
	@./scripts/smoke_auth.sh


# =============================================================================
# CI / Guards (opinionated)
# =============================================================================
.PHONY: ci-tools guard-no-hardcoded-8000 guard-no-pytest-detection guard-stream-markers build-sidecar ci

ci-tools:
	@command -v rg >/dev/null || (echo "❌ rg missing" && exit 1)
	@command -v curl >/dev/null || (echo "❌ curl missing" && exit 1)
	@command -v sqlite3 >/dev/null || (echo "❌ sqlite3 missing" && exit 1)
	@echo "✅ CI tools present"

# Avoid banning docs/examples/snapshots. Enforce only in runtime code.
guard-no-hardcoded-8000:
	@rg -n "127\.0\.0\.1:8000|:8000\b" api scripts/uvicorn_local.sh 2>/dev/null && \
	 (echo "❌ Hardcoded :8000 found in runtime code. Use HOST/PORT/BASE_URL." && exit 1) || \
	 echo "✅ No hardcoded :8000 in runtime code"

guard-no-pytest-detection:
	@rg -n "_running_under_pytest|PYTEST_CURRENT_TEST|sys\.modules\['pytest'\]" api/main.py >/dev/null && \
	 (echo "❌ Pytest-detection found in api/main.py. Remove test hacks." && exit 1) || \
	 echo "✅ No pytest-detection in api/main.py"

guard-stream-markers:
	@./scripts/guard_feed_stream_markers.sh

build-sidecar:
	@cd supervisor-sidecar && go build ./...

# Default CI lane: fast, deterministic. HTTP e2e can be separate job/workflow.
ci: ci-tools guard-no-hardcoded-8000 guard-no-pytest-detection guard-stream-markers fg-fast build-sidecar itest-localI will get those KB's pulled

# =============================================================================
# Legacy aliases (keep docs/fingers intact)
# =============================================================================
.PHONY: up-local down-local restart-local logs-local ready-local health check test


# =============================================================================
# Roll Back Last Patch
# =============================================================================

fg-rollback-last-patch:
	@set -e; \
	last="$$(ls -1dt artifacts/patch_backups/* 2>/dev/null | head -n 1)"; \
	test -n "$$last" || (echo "No backups found" && exit 1); \
	echo "Rolling back from $$last"; \
	cp -a "$$last/main.py" api/main.py; \
	echo "Restored api/main.py"; \
	git status --porcelain

# =============================================================================
# Intergration Tests
# =============================================================================

.PHONY: test-integration
test-integration:
	@echo "== integration tests =="
	@test -n "$${BASE_URL:-}" || (echo "❌ BASE_URL is required" && exit 1)
	@test -n "$${FG_SQLITE_PATH:-}" || (echo "❌ FG_SQLITE_PATH is required (path to sqlite db)" && exit 1)
	@test -n "$${FG_API_KEY:-}" || (echo "❌ FG_API_KEY is required" && exit 1)
	@FG_BASE_URL="$${BASE_URL}" pytest -q -m integration

.PHONY: itest-local
itest-local:
	@set -euo pipefail; \
	mkdir -p state artifacts; \
	export HOST=127.0.0.1 PORT=8001; \
	export BASE_URL=$${BASE_URL:-http://$${HOST}:$${PORT}}; \
	export FG_ENV=$${FG_ENV:-dev}; \
	export FG_AUTH_ENABLED=$${FG_AUTH_ENABLED:-1}; \
	export FG_API_KEY=$${FG_API_KEY:-supersecret}; \
	export FG_SQLITE_PATH=$${FG_SQLITE_PATH:-$$(pwd)/state/frostgate-itest.db}; \
	python -c "from api.db import init_db; init_db()"; \
	nohup bash -lc ' \
	  set -e; \
	  FG_ENV="'"$${FG_ENV}"'" \
	  FG_AUTH_ENABLED="'"$${FG_AUTH_ENABLED}"'" \
	  FG_API_KEY="'"$${FG_API_KEY}"'" \
	  FG_SQLITE_PATH="'"$${FG_SQLITE_PATH}"'" \
	  uvicorn api.main:app --host "'"$${HOST}"'" --port "'"$${PORT}"'" \
	' > artifacts/uvicorn-itest.log 2>&1 & \
	echo $$! > artifacts/uvicorn-itest.pid; \
	trap 'kill $$(cat artifacts/uvicorn-itest.pid 2>/dev/null) 2>/dev/null || true' EXIT; \
	for i in $$(seq 1 60); do \
	  curl -fsS "$${BASE_URL}/health" >/dev/null && break; \
	  sleep 0.5; \
	done; \
	curl -fsS "$${BASE_URL}/health" >/dev/null || (echo "API failed to start"; tail -200 artifacts/uvicorn-itest.log || true; exit 1); \
	./scripts/smoke_auth.sh; \
	$(MAKE) test-integration BASE_URL="$${BASE_URL}"


up-local: fg-up
down-local: fg-down
restart-local: fg-restart
logs-local: fg-logs
ready-local: fg-ready
health: fg-health
check: fg-fast
test: fg-fast
