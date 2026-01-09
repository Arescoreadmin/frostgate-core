# =============================================================================
# FrostGate Core - Makefile
# Production-grade / single source of truth / no drift
# =============================================================================

SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -euo pipefail -c
.DELETE_ON_ERROR:

# -----------------------------------------------------------------------------
# Repo + Python
# -----------------------------------------------------------------------------
VENV   ?= .venv
PY     := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
export PYTHONPATH := .

# Deterministic tests (local + CI)
PYTEST_ENV := env PYTHONHASHSEED=0 TZ=UTC

# -----------------------------------------------------------------------------
# Runtime defaults (single source of truth)
# -----------------------------------------------------------------------------
HOST     ?= 127.0.0.1
PORT     ?= 8000
BASE_URL ?= http://$(HOST):$(PORT)

FG_ENV                  ?= dev
FG_SERVICE              ?= frostgate-core
FG_AUTH_ENABLED         ?= 1
FG_API_KEY              ?= supersecret
FG_ENFORCEMENT_MODE     ?= observe
FG_DEV_EVENTS_ENABLED   ?= 0
FG_UI_TOKEN_GET_ENABLED ?= 1

# State / artifacts
ARTIFACTS_DIR ?= artifacts
STATE_DIR     ?= state

# Canonical state dir for local runs (logs, pid, db)
FG_STATE_DIR   ?= $(CURDIR)/$(ARTIFACTS_DIR)
FG_SQLITE_PATH ?= $(FG_STATE_DIR)/frostgate.db

# Legacy mirror (some scripts/tests read API_KEY)
export API_KEY := $(FG_API_KEY)

# -----------------------------------------------------------------------------
# Centralized env injector (single source of truth)
# Use: $(FG_RUN) <command>
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
	  "Guards / audits:" \
	  "  make guard-scripts       Prevent paste-garbage + Makefile sanity" \
	  "  make fg-audit-make       Makefile target collision audit" \
	  "  make fg-contract         Contract linter" \
	  "  make fg-compile          py_compile core entrypoints" \
	  "" \
	  "Fast lane (no server):" \
	  "  make fg-fast             audit + contract + compile + pytest + lint" \
	  "" \
	  "Local server:" \
	  "  make fg-live-port-check  fail if HOST:PORT already bound" \
	  "  make fg-up               start uvicorn + wait ready" \
	  "  make fg-down             stop uvicorn" \
	  "  make fg-ready            wait /health/ready" \
	  "  make fg-health           GET /health" \
	  "  make fg-logs N=200       tail uvicorn log" \
	  "" \
	  "Tests:" \
	  "  make test-clean          contract+compile+pytest (plus spine)" \
	  "  make test-spine          spine-only suite" \
	  "  make test-strict         warnings-as-errors pytest" \
	  "" \
	  "Integration:" \
	  "  make itest-local         run isolated server on :8001 + integration tests" \
	  "  make itest-up            bring itest server up (no tests)" \
	  "  make itest-down          stop itest server" \
	  "" \
	  "No drift:" \
	  "  make no-drift            guards + itest-local + pytest + git clean check" \
	  "" \
	  "CI:" \
	  "  make ci                  unit lane (fg-fast)" \
	  "  make ci-integration      integration lane (itest-local)" \
	  "  make ci-evidence         evidence lane (itest-up + smoke + evidence)" \
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
# Guards / audits (always run via $(PY), never as executables)
# =============================================================================
.PHONY: guard-scripts fg-audit-make fg-contract fg-compile

guard-scripts:
	@$(PY) scripts/guard_no_paste_garbage.py
	@$(PY) scripts/guard_makefile_sanity.py

fg-audit-make: guard-scripts
	@$(PY) scripts/audit_make_targets.py

fg-contract: guard-scripts
	@$(PY) scripts/contract_lint.py

fg-compile: guard-scripts
	@$(PY) -m py_compile api/main.py api/feed.py api/ui.py api/dev_events.py api/auth_scopes.py

# =============================================================================
# Lint
# =============================================================================
.PHONY: fg-lint
fg-lint:
	@$(PY) -m py_compile api/middleware/auth_gate.py
	@$(PY) -m ruff check api tests
	@$(PY) -m ruff format --check api tests

# =============================================================================
# Fast lane (no server)
# =============================================================================
.PHONY: fg-fast
fg-fast: fg-audit-make fg-contract fg-compile
	@$(PYTEST_ENV) $(PY) -m pytest -q
	@$(MAKE) -s fg-lint

# =============================================================================
# Live port guard (no heredoc; paste-safe)
# =============================================================================
.PHONY: fg-live-port-check
fg-live-port-check:
	@set -euo pipefail; \
	h="$(HOST)"; p="$(PORT)"; \
	python -c 'import socket,sys,subprocess,shutil; h=sys.argv[1]; p=int(sys.argv[2]); s=socket.socket(socket.AF_INET,socket.SOCK_STREAM); s.settimeout(0.25); rc=s.connect_ex((h,p)); s.close(); \
	( print(f"✅ Port free: {h}:{p}") or sys.exit(0) ) if rc!=0 else None; \
	print(f"❌ Refusing to start: {h}:{p} already has a listener"); \
	( print(subprocess.check_output(["lsof","-nP",f"-iTCP:{p}","-sTCP:LISTEN"],stderr=subprocess.DEVNULL,text=True).strip() or "") ) if shutil.which("lsof") else print("(lsof not available; cannot identify owning process)"); \
	sys.exit(1)' "$$h" "$$p"

# =============================================================================
# Local server (canonical)
# =============================================================================
.PHONY: fg-up fg-down fg-restart fg-ready fg-health fg-logs fg-status

fg-up: fg-live-port-check
	mkdir -p "$(FG_STATE_DIR)" "$(STATE_DIR)"
	$(FG_RUN) ./scripts/uvicorn_local.sh start
	$(MAKE) -s fg-ready

fg-down:
	$(FG_RUN) ./scripts/uvicorn_local.sh stop || true

fg-restart: fg-live-port-check
	mkdir -p "$(FG_STATE_DIR)" "$(STATE_DIR)"
	$(FG_RUN) ./scripts/uvicorn_local.sh restart
	$(MAKE) -s fg-ready

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
# Integration tests (expects API running at BASE_URL)
# =============================================================================
.PHONY: test-integration
test-integration:
	@echo "== integration tests =="
	@test -n "$${BASE_URL:-}" || (echo "❌ BASE_URL is required" && exit 1)
	@test -n "$${FG_SQLITE_PATH:-}" || (echo "❌ FG_SQLITE_PATH is required" && exit 1)
	@test -n "$${FG_API_KEY:-}" || (echo "❌ FG_API_KEY is required" && exit 1)
	@FG_BASE_URL="$${BASE_URL}" $(PYTEST_ENV) $(PY) -m pytest -q -m integration

# =============================================================================
# Integration test run (deterministic, no drift, no zombie reuse)
# =============================================================================
ITEST_HOST     ?= 127.0.0.1
ITEST_PORT     ?= 8001
ITEST_BASE_URL ?= http://$(ITEST_HOST):$(ITEST_PORT)
ITEST_DB       ?= $(CURDIR)/$(STATE_DIR)/frostgate-itest.db
ITEST_WIPE_DB  ?= 1

.PHONY: itest-local itest-down itest-up itest-db-reset

itest-db-reset:
	@set -euo pipefail; \
	mkdir -p "$(STATE_DIR)"; \
	if [ "$(ITEST_WIPE_DB)" = "1" ]; then rm -f "$(ITEST_DB)"; fi; \
	FG_SQLITE_PATH="$(ITEST_DB)" $(PY) -c "from api.db import init_db; init_db()"; \
	echo "✅ itest db ready: $(ITEST_DB)"

itest-down:
	@set -euo pipefail; \
	$(MAKE) -s fg-down \
	  HOST="$(ITEST_HOST)" PORT="$(ITEST_PORT)" BASE_URL="$(ITEST_BASE_URL)" \
	  FG_SQLITE_PATH="$(ITEST_DB)" FG_API_KEY="$(FG_API_KEY)" FG_AUTH_ENABLED="$(FG_AUTH_ENABLED)" \
	  >/dev/null 2>&1 || true; \
	echo "✅ itest server stopped (or was not running)"

itest-up: itest-db-reset
	@set -euo pipefail; \
	$(MAKE) -s fg-up \
	  HOST="$(ITEST_HOST)" PORT="$(ITEST_PORT)" BASE_URL="$(ITEST_BASE_URL)" \
	  FG_SQLITE_PATH="$(ITEST_DB)" FG_API_KEY="$(FG_API_KEY)" FG_AUTH_ENABLED="$(FG_AUTH_ENABLED)"; \
	echo "✅ itest server up: $(ITEST_BASE_URL)"

itest-local: itest-down itest-up
	@set -euo pipefail; \
	trap '$(MAKE) -s itest-down >/dev/null 2>&1 || true' EXIT; \
	BASE_URL="$(ITEST_BASE_URL)" FG_API_KEY="$(FG_API_KEY)" FG_SQLITE_PATH="$(ITEST_DB)" ./scripts/smoke_auth.sh; \
	BASE_URL="$(ITEST_BASE_URL)" FG_API_KEY="$(FG_API_KEY)" FG_SQLITE_PATH="$(ITEST_DB)" $(MAKE) -s test-integration; \
	echo "✅ itest-local OK"

# =============================================================================
# Tests
# =============================================================================
.PHONY: test-spine test-clean test-strict test-guard

test-guard:
	@$(PY) scripts/guard_pytest_ini.py

test-spine: test-guard
	@$(PY) -m py_compile api/main.py api/forensics.py api/governance.py api/mission_envelope.py api/ring_router.py api/roe_engine.py api/schemas_impact.py
	@env -u FG_DB_URL -u FG_SQLITE_PATH -u FG_STATE_DIR -u FG_ENV $(PYTEST_ENV) $(PY) -m pytest -q \
		tests/test_forensic_snapshot_replay.py \
		tests/test_governance_approval_flow.py \
		tests/test_mission_envelope_contract.py \
		tests/test_ring_router_contract.py \
		tests/test_roe_gating_contract.py

test-clean: test-guard
	@set -euo pipefail; \
	if command -v npx >/dev/null 2>&1; then \
		(npx -y markdownlint-cli CONTRACT.md >/dev/null 2>&1 && echo "✅ markdownlint OK") || echo "⚠️ markdownlint unavailable; skipping"; \
	else \
		echo "⚠️ npx not installed; skipping markdownlint"; \
	fi
	@$(PY) -m py_compile api/db.py api/auth_scopes.py tests/conftest.py backend/tests/conftest.py
	@env -u FG_DB_URL -u FG_SQLITE_PATH -u FG_STATE_DIR -u FG_ENV $(PYTEST_ENV) $(PY) -m pytest -q
	@$(MAKE) -s test-spine

test-strict: test-guard
	@$(PYTEST_ENV) $(PY) -W error -m pytest -q

# =============================================================================
# No drift: "new terminal sanity button"
# =============================================================================
.PHONY: no-drift no-drift-check-clean
no-drift: guard-scripts itest-local
	@$(PYTEST_ENV) $(PY) -m pytest -q
	@$(MAKE) -s no-drift-check-clean
	@echo "✅ no-drift OK"

no-drift-check-clean:
	@echo "== no-drift: git clean check =="; \
	st="$$(git status --porcelain)"; \
	if [ -n "$$st" ]; then \
		echo "❌ Working tree is dirty after no-drift run:"; \
		echo "$$st"; \
		exit 1; \
	fi

# =============================================================================
# CI lanes (single source of truth)
# =============================================================================
.PHONY: ci ci-integration
ci: fg-fast
	@echo "✅ CI unit lane OK"

ci-integration: itest-local
	@echo "✅ CI integration lane OK"

# =============================================================================
# Evidence bundle (signed)
# =============================================================================
EVIDENCE_SCENARIO ?= $(or $(SCENARIO),spike)

.PHONY: evidence
evidence:
	@set -euo pipefail; \
	test -n "$${BASE_URL:-}" || (echo "❌ BASE_URL is required" && exit 1); \
	test -n "$${FG_API_KEY:-}" || (echo "❌ FG_API_KEY is required" && exit 1); \
	test -n "$${FG_SQLITE_PATH:-}" || (echo "❌ FG_SQLITE_PATH is required" && exit 1); \
	mkdir -p "$(ARTIFACTS_DIR)" "$(STATE_DIR)" keys; \
	ts="$$(date -u +%Y%m%dT%H%M%SZ)"; \
	out="$(ARTIFACTS_DIR)/evidence_$${ts}_$${EVIDENCE_SCENARIO}"; \
	mkdir -p "$$out"; \
	echo "$$out" > "$(ARTIFACTS_DIR)/latest_evidence_dir.txt"; \
	echo "$${EVIDENCE_SCENARIO}" > "$$out/scenario.txt"; \
	git rev-parse HEAD > "$$out/git_head.txt"; \
	git status --porcelain=v1 > "$$out/git_status.txt" || true; \
	curl -fsS "$${BASE_URL}/health" > "$$out/health.json"; \
	curl -fsS "$${BASE_URL}/health/ready" > "$$out/health_ready.json" || true; \
	curl -fsS "$${BASE_URL}/openapi.json" > "$$out/openapi.json" || true; \
	cp -f CONTRACT.md "$$out/CONTRACT.md" 2>/dev/null || true; \
	cp -f README.md "$$out/README.md" 2>/dev/null || true; \
	( command -v sha256sum >/dev/null 2>&1 ) || (echo "❌ sha256sum missing" && exit 1); \
	( cd "$$out" && find . -type f -maxdepth 2 -print0 | sort -z | xargs -0 sha256sum > manifest.sha256 ); \
	( command -v minisign >/dev/null 2>&1 ) || (echo "❌ minisign missing (install it)" && exit 1); \
	if [ -n "$${MINISIGN_SECRET_KEY:-}" ]; then \
		printf "%s\n" "$${MINISIGN_SECRET_KEY}" > keys/minisign.key; \
		chmod 600 keys/minisign.key; \
		sec="keys/minisign.key"; \
	elif [ -f minisign.key ]; then \
		sec="minisign.key"; \
	else \
		echo "❌ No signing key. Provide MINISIGN_SECRET_KEY or minisign.key"; \
		exit 1; \
	fi; \
	minisign -Sm "$$out/manifest.sha256" -s "$$sec"; \
	zipname="$(ARTIFACTS_DIR)/frostgate_evidence_$${ts}_$${EVIDENCE_SCENARIO}.zip"; \
	( cd "$(ARTIFACTS_DIR)" && zip -qr "$$(basename "$$zipname")" "$$(basename "$$out")" ); \
	echo "$$zipname" > "$(ARTIFACTS_DIR)/latest_zip.txt"; \
	echo "✅ evidence zip: $$zipname"

# =============================================================================
# CI Evidence lane (start itest server, smoke, evidence, stop)
# =============================================================================
.PHONY: ci-evidence
ci-evidence:
	@set -euo pipefail; \
	$(MAKE) -s itest-down >/dev/null 2>&1 || true; \
	$(MAKE) -s itest-up; \
	trap '$(MAKE) -s itest-down >/dev/null 2>&1 || true' EXIT; \
	BASE_URL="$(ITEST_BASE_URL)" FG_API_KEY="$(FG_API_KEY)" FG_SQLITE_PATH="$(ITEST_DB)" ./scripts/smoke_auth.sh; \
	SCENARIO="$${SCENARIO:-spike}" BASE_URL="$(ITEST_BASE_URL)" FG_API_KEY="$(FG_API_KEY)" FG_SQLITE_PATH="$(ITEST_DB)" $(MAKE) -s evidence; \
	echo "✅ CI evidence lane OK"

# =============================================================================
# Doctor
# =============================================================================
.PHONY: doctor
doctor: guard-scripts
	@$(PY) -m py_compile api/main.py api/db.py
	@$(PY) scripts/find_bad_toml.py
	@$(MAKE) -s fg-audit-make
	@$(MAKE) -s test-clean
	@echo "✅ doctor OK"

# =============================================================================
# Convenience: run integration marker against a running itest-up server
# =============================================================================
.PHONY: itest-integration
itest-integration: itest-up
	@set -euo pipefail; \
	trap '$(MAKE) -s itest-down >/dev/null 2>&1 || true' EXIT; \
	BASE_URL="$(BASE_URL)" FG_API_KEY="$(FG_API_KEY)" FG_SQLITE_PATH="$(FG_SQLITE_PATH)" \
	$(PYTEST_ENV) $(PY) -m pytest -q -m integration; \
	echo "✅ itest-integration OK"
