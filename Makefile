# =============================================================================
# FrostGate Core - Makefile (professional / no-drift)
# =============================================================================

SHELL := /bin/bash
.SHELLFLAGS := -lc
.ONESHELL:
.DELETE_ON_ERROR:

# =============================================================================
# Global / Build Metadata
# =============================================================================

REGISTRY        ?= ghcr.io
IMAGE_OWNER     ?= your-org-or-user
CORE_IMAGE_NAME ?= frostgate-core
SIDE_IMAGE_NAME ?= frostgate-supervisor-sidecar

CORE_IMAGE      := $(REGISTRY)/$(IMAGE_OWNER)/$(CORE_IMAGE_NAME)
SIDE_IMAGE      := $(REGISTRY)/$(IMAGE_OWNER)/$(SIDE_IMAGE_NAME)

VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)

# =============================================================================
# Python / Venv
# =============================================================================

VENV   ?= .venv
PYTHON ?= $(VENV)/bin/python
PIP    ?= $(VENV)/bin/pip
export PYTHONPATH := .

# =============================================================================
# Runtime Defaults (Single Source of Truth)
# =============================================================================

HOST     ?= 127.0.0.1
PORT     ?= 8000
BASE_URL ?= http://$(HOST):$(PORT)

FG_ENV              ?= dev
FG_SERVICE          ?= frostgate-core
FG_AUTH_ENABLED     ?= 1
FG_API_KEY          ?= demo_key_change_me
FG_ENFORCEMENT_MODE ?= observe

# State (PIN THIS or you'll keep "losing" your DB)
FG_STATE_DIR   ?= $(CURDIR)/artifacts
FG_SQLITE_PATH ?= $(FG_STATE_DIR)/frostgate.db

# Legacy mirror
export API_KEY := $(FG_API_KEY)

# Artifacts
ARTIFACTS_DIR ?= artifacts
EVIDENCE_DIR  ?= $(ARTIFACTS_DIR)/evidence
SCENARIO      ?= spike

# =============================================================================
# Env injector (centralized)
# =============================================================================
define FG_RUN
FG_ENV="$(FG_ENV)" \
FG_SERVICE="$(FG_SERVICE)" \
FG_AUTH_ENABLED="$(FG_AUTH_ENABLED)" \
FG_API_KEY="$(FG_API_KEY)" \
FG_ENFORCEMENT_MODE="$(FG_ENFORCEMENT_MODE)" \
FG_STATE_DIR="$(FG_STATE_DIR)" \
FG_SQLITE_PATH="$(FG_SQLITE_PATH)" \
BASE_URL="$(BASE_URL)" \
HOST="$(HOST)" \
PORT="$(PORT)" \
API_KEY="$(FG_API_KEY)"
endef

# =============================================================================
# Help
# =============================================================================
.PHONY: help
help:
	@echo "FrostGate Core - Targets"
	@echo ""
	@echo "Setup:"
	@echo "  make venv                Create venv + install deps"
	@echo ""
	@echo "Local API (no drift):"
	@echo "  make up-local            Start pinned local API (PID+log under artifacts/)"
	@echo "  make down-local          Stop pinned local API"
	@echo "  make status-local        Show pid, health, sqlite path, artifacts"
	@echo "  make logs-local          Tail logs"
	@echo "  make kill-uvicorn        Kill strays (ports, pids, optional docker)"
	@echo ""
	@echo "Dev run (reload):"
	@echo "  make run-dev             Uvicorn --reload using pinned state"
	@echo "  make stop-dev            Kill listener bound to PORT"
	@echo ""
	@echo "Tests:"
	@echo "  make test                Unit lane"
	@echo "  make test-smoke           Smoke lane"
	@echo "  make test-integration     Integration lane (requires running API)"
	@echo "  make test-e2e             Integration+e2e markers (requires running API)"
	@echo "  make test-all             Unit + integration + e2e"
	@echo ""
	@echo "Demo / Evidence:"
	@echo "  make demo                Seed + build HTML report"
	@echo "  make evidence             Evidence export + zip (+ optional signature)"
	@echo ""
	@echo "E2E local lane:"
	@echo "  make e2e-local            Unit -> start -> integration -> evidence -> stop"
	@echo ""
	@echo "Overrides:"
	@echo "  HOST=... PORT=... FG_API_KEY=... FG_STATE_DIR=... FG_SQLITE_PATH=... SCENARIO=spike"
	@echo ""

# =============================================================================
# Setup
# =============================================================================
.PHONY: venv
venv:
	test -d "$(VENV)" || python -m venv "$(VENV)"
	"$(PIP)" install --upgrade pip
	"$(PIP)" install -r requirements.txt -r requirements-dev.txt

# =============================================================================
# Local API Runner (No Drift)
# =============================================================================

.PHONY: ready-local up-local down-local logs-local status-local

ready-local:
	@set -euo pipefail; \
	timeout=30; \
	for i in $$(seq 1 $$timeout); do \
		if curl -fsS "$(BASE_URL)/health/ready" >/dev/null 2>&1; then \
			echo "✅ /health/ready OK"; exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "❌ Timed out waiting for /health/ready"; \
	test -f "$(FG_STATE_DIR)/uvicorn.local.log" && tail -n 60 "$(FG_STATE_DIR)/uvicorn.local.log" || true; \
	exit 1

up-local:
	@set -euo pipefail; \
	mkdir -p "$(FG_STATE_DIR)"; \
	pidfile="$(FG_STATE_DIR)/uvicorn.local.pid"; \
	logfile="$(FG_STATE_DIR)/uvicorn.local.log"; \
	if test -f "$$pidfile"; then \
		pid="$$(cat "$$pidfile" 2>/dev/null || true)"; \
		if test -n "$$pid" && kill -0 "$$pid" >/dev/null 2>&1; then \
			echo "✅ already running (pid=$$pid)"; exit 0; \
		fi; \
		rm -f "$$pidfile"; \
	fi; \
	# kill anything else binding the port (your user only)
	lsof -t -iTCP:$(PORT) -sTCP:LISTEN 2>/dev/null | xargs -r kill >/dev/null 2>&1 || true; \
	$(FG_RUN) nohup "$(PYTHON)" -m uvicorn api.main:app \
		--host "$(HOST)" --port "$(PORT)" --log-level debug \
		> "$$logfile" 2>&1 & \
	echo $$! > "$$pidfile"; \
	echo "🚀 started (pid=$$(cat "$$pidfile")) log=$$logfile"; \
	$(MAKE) -s ready-local

down-local:
	@set -euo pipefail; \
	pidfile="$(FG_STATE_DIR)/uvicorn.local.pid"; \
	if test -f "$$pidfile"; then \
		pid="$$(cat "$$pidfile" 2>/dev/null || true)"; \
		if test -n "$$pid" && kill -0 "$$pid" >/dev/null 2>&1; then \
			kill "$$pid" >/dev/null 2>&1 || true; \
			sleep 0.3; \
			kill -9 "$$pid" >/dev/null 2>&1 || true; \
			echo "🧹 stopped (pid=$$pid)"; \
		fi; \
		rm -f "$$pidfile"; \
	else \
		echo "ℹ️ not running"; \
	fi; \
	lsof -iTCP:$(PORT) -sTCP:LISTEN -nP || true

no-drift:
	@bash scripts/no_drift.sh

snapshot:
	@bash scripts/snapshot_context.sh


logs-local:
	@set -euo pipefail; \
	test -f "$(FG_STATE_DIR)/uvicorn.local.log" || { echo "no log file"; exit 1; }; \
	tail -n 200 "$(FG_STATE_DIR)/uvicorn.local.log"

status-local:
	@set -euo pipefail; \
	echo "BASE_URL=$(BASE_URL)"; \
	echo "FG_STATE_DIR=$(FG_STATE_DIR)"; \
	echo "FG_SQLITE_PATH=$(FG_SQLITE_PATH)"; \
	test -f "$(FG_STATE_DIR)/uvicorn.local.pid" && echo "PID=$$(cat "$(FG_STATE_DIR)/uvicorn.local.pid")" || echo "PID=(none)"; \
	curl -fsS "$(BASE_URL)/health/live" || true; echo; \
	curl -fsS "$(BASE_URL)/health/ready" || true; echo; \
	ls -lah "$(FG_STATE_DIR)" 2>/dev/null || true

# =============================================================================
# Kill Utilities (Strays / Ports / Optional Docker)
# =============================================================================

.PHONY: kill-uvicorn
kill-uvicorn:
	-@echo "Killing stray uvicorn processes..."
	-@lsof -t -iTCP:8000 -sTCP:LISTEN 2>/dev/null | xargs -r kill || true
	-@lsof -t -iTCP:8080 -sTCP:LISTEN 2>/dev/null | xargs -r kill || true
	-@lsof -t -iTCP:18080 -sTCP:LISTEN 2>/dev/null | xargs -r kill || true
	-@pkill -f "uvicorn api.main:app" || true
	-@pkill -f "python -m uvicorn api.main:app" || true
	-@pkill -f ".venv/bin/uvicorn api.main:app" || true
	# optional docker cleanup (only if you want it)
	-@sudo docker ps --format '{{.ID}} {{.Image}} {{.Names}}' | rg -i 'frostgate-core|uvicorn|app\.main|api\.main' | awk '{print $$1}' | xargs -r sudo docker rm -f || true
	-@lsof -iTCP:8000 -sTCP:LISTEN -nP || true
	-@lsof -iTCP:8080 -sTCP:LISTEN -nP || true
	-@lsof -iTCP:18080 -sTCP:LISTEN -nP || true

# =============================================================================
# Dev Run (Reload)
# =============================================================================

.PHONY: port-check run-dev stop-dev quickcheck

port-check:
	@ss -ltn "sport = :$(PORT)" | rg -q LISTEN && \
		(echo "❌ Port $(PORT) in use. Run: make kill-uvicorn or change PORT=..." && exit 1) || true

run-dev: port-check
	@mkdir -p "$(FG_STATE_DIR)"
	@echo "Starting FrostGate Core on $(BASE_URL)"
	@$(FG_RUN) uvicorn api.main:app --host "$(HOST)" --port "$(PORT)" --reload

stop-dev:
	@pids=$$(ss -ltnp "sport = :$(PORT)" 2>/dev/null | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u); \
	if [ -z "$$pids" ]; then echo "No listener on $(PORT)"; exit 0; fi; \
	echo "Killing listeners on $(PORT): $$pids"; \
	kill -TERM $$pids 2>/dev/null || true; \
	sleep 1; \
	kill -KILL $$pids 2>/dev/null || true

quickcheck:
	@curl -fsS "$(BASE_URL)/health/live" | python -m json.tool
	@curl -fsS "$(BASE_URL)/health/ready" | python -m json.tool || true

# =============================================================================
# Tests
# =============================================================================

.PHONY: test test-sanity test-smoke test-integration test-e2e test-all

test:
	"$(PYTHON)" -m pytest -q -m "not integration"

test-sanity:
	"$(PYTHON)" -c "import pytest, pytest_asyncio, pytest_env; print('pytest', pytest.__version__, 'pytest_asyncio', pytest_asyncio.__version__)"
	$(MAKE) test

test-smoke:
	"$(PYTHON)" -m pytest -q -m smoke

server-check:
	@curl -fsS "$(BASE_URL)/health/ready" >/dev/null || (echo "❌ API not reachable at $(BASE_URL)"; exit 1)
	@curl -fsS -H "X-API-Key: $(FG_API_KEY)" "$(BASE_URL)/stats/summary" >/dev/null || (echo "❌ API reachable but auth failed. Check FG_API_KEY / FG_AUTH_ENABLED."; exit 1)

test-integration: server-check
	@$(FG_RUN) "$(PYTHON)" -m pytest -q -m integration

test-e2e: server-check
	@$(FG_RUN) "$(PYTHON)" -m pytest -q -m "integration or e2e"

test-all: test test-integration test-e2e

# =============================================================================
# Seed / Stats
# =============================================================================

.PHONY: seed-wipe stats-summary seed seed-spike seed-steady seed-drop seed-%

seed-wipe:
	@mkdir -p "$(FG_STATE_DIR)"
	@sqlite3 "$(FG_SQLITE_PATH)" "delete from decisions;" || true

stats-summary:
	@curl -fsS -H "X-API-Key: $(FG_API_KEY)" "$(BASE_URL)/stats/summary" | python -m json.tool

seed: server-check seed-wipe
	@test -n "$(SEED_MODE)" || (echo "❌ SEED_MODE required (spike|steady|drop)" && exit 1)
	@$(FG_RUN) SEED_MODE="$(SEED_MODE)" ./scripts/seed_demo_decisions.sh
	@$(MAKE) stats-summary

seed-spike: ; @$(MAKE) seed SEED_MODE=spike
seed-steady: ; @$(MAKE) seed SEED_MODE=steady
seed-drop:   ; @$(MAKE) seed SEED_MODE=drop
seed-%:      ; @$(MAKE) seed SEED_MODE="$*"

# =============================================================================
# Demo / Artifacts
# =============================================================================

.PHONY: demo demo-report demo-open demo-clean

demo: server-check seed-$(SCENARIO) demo-report
	@echo "✅ Demo complete: $(ARTIFACTS_DIR)"

demo-report:
	@ts=$$(date -u +%Y%m%dT%H%M%SZ); \
	out_dir="$(ARTIFACTS_DIR)/$${ts}_$(SCENARIO)"; \
	mkdir -p "$$out_dir"; \
	echo "Writing report -> $$out_dir"; \
	curl -fsS -H "X-API-Key: $(FG_API_KEY)" "$(BASE_URL)/health/ready"   > "$$out_dir/health.json"; \
	curl -fsS -H "X-API-Key: $(FG_API_KEY)" "$(BASE_URL)/stats/summary"  > "$$out_dir/summary.json"; \
	curl -fsS -H "X-API-Key: $(FG_API_KEY)" "$(BASE_URL)/stats"          > "$$out_dir/stats.json"; \
	printf '<html><head><meta charset="utf-8"><title>FrostGate Demo Report</title></head><body>' > "$$out_dir/report.html"; \
	printf '<h1>FrostGate Demo Report</h1><p><b>Scenario:</b> $(SCENARIO)</p><p><b>Generated:</b> %s</p>' "$$ts" >> "$$out_dir/report.html"; \
	printf '<h2>/stats/summary</h2><pre>' >> "$$out_dir/report.html"; \
	python -m json.tool < "$$out_dir/summary.json" >> "$$out_dir/report.html"; \
	printf '</pre><h2>/health/ready</h2><pre>' >> "$$out_dir/report.html"; \
	python -m json.tool < "$$out_dir/health.json" >> "$$out_dir/report.html"; \
	printf '</pre><h2>/stats</h2><pre>' >> "$$out_dir/report.html"; \
	python -m json.tool < "$$out_dir/stats.json" >> "$$out_dir/report.html"; \
	printf '</pre></body></html>' >> "$$out_dir/report.html"; \
	echo "$$out_dir" > "$(ARTIFACTS_DIR)/latest.txt"; \
	echo "Latest report: $$out_dir/report.html"

demo-open:
	@dir=$$(cat "$(ARTIFACTS_DIR)/latest.txt" 2>/dev/null || true); \
	test -n "$$dir" || (echo "No latest report. Run: make demo"; exit 1); \
	xdg-open "$$dir/report.html" >/dev/null 2>&1 || true; \
	echo "Opened: $$dir/report.html"

demo-clean:
	rm -rf "$(ARTIFACTS_DIR)"

# =============================================================================
# Evidence Bundle
# =============================================================================

.PHONY: evidence evidence-report evidence-sign evidence-zip evidence-open evidence-verify

evidence: server-check seed-$(SCENARIO) evidence-report evidence-sign evidence-zip
	@echo "✅ Evidence bundle complete:"
	@cat "$(ARTIFACTS_DIR)/latest_zip.txt"

evidence-report:
	@$(FG_RUN) ARTIFACTS_DIR="$(ARTIFACTS_DIR)" EVIDENCE_DIR="$(EVIDENCE_DIR)" SCENARIO="$(SCENARIO)" ./scripts/evidence_report.sh

evidence-sign:
	@bash -lc '\
	  set -euo pipefail; \
	  out=$$(cat "$(ARTIFACTS_DIR)/latest_evidence_dir.txt"); \
	  test -f "$$out/manifest.sha256" || { echo "❌ manifest missing"; exit 1; }; \
	  if [ -n "$${MINISIGN_SECRET_KEY:-}" ]; then \
	    echo "Signing manifest.sha256"; \
	    printf "%s" "$$MINISIGN_SECRET_KEY" > /tmp/minisign.key; \
	    minisign -S -s /tmp/minisign.key -m "$$out/manifest.sha256"; \
	    rm -f /tmp/minisign.key; \
	    test -f "$$out/manifest.sha256.minisig" || { echo "❌ signature not created"; exit 1; }; \
	  else \
	    echo "MINISIGN_SECRET_KEY not set, skipping signature"; \
	  fi; \
	  echo "✅ Evidence manifest signing complete"; \
	'

evidence-zip:
	@bash -lc '\
	  set -euo pipefail; \
	  out=$$(cat "$(ARTIFACTS_DIR)/latest_evidence_dir.txt"); \
	  ts=$$(basename "$$out" | cut -d_ -f1); \
	  scen=$$(echo "$(SCENARIO)" | tr -d "[:space:]"); \
	  zipname="$(ARTIFACTS_DIR)/frostgate_evidence_$${ts}_$${scen}.zip"; \
	  rm -f "$$zipname"; \
	  (cd "$$out/.." && zip -r "../$$(basename "$$zipname")" "$$(basename "$$out")" >/dev/null); \
	  echo "$$zipname" > "$(ARTIFACTS_DIR)/latest_zip.txt"; \
	  ls -lh "$$zipname"; \
	'

evidence-open:
	@out=$$(cat "$(ARTIFACTS_DIR)/latest_evidence_dir.txt" 2>/dev/null || true); \
	test -n "$$out" || (echo "No latest evidence. Run: make evidence"; exit 1); \
	ls -lah "$$out"

evidence-verify:
	@bash -lc '\
	  set -euo pipefail; \
	  zip=$$(cat "$(ARTIFACTS_DIR)/latest_zip.txt"); \
	  tmp=$$(mktemp -d); \
	  unzip -q "$$zip" -d "$$tmp"; \
	  dir=$$(find "$$tmp" -maxdepth 2 -type d -name "*_$(SCENARIO)" | head -n1); \
	  test -n "$$dir" || (echo "❌ evidence dir not found in zip" && exit 1); \
	  (cd "$$dir" && sha256sum -c manifest.sha256); \
	  echo "✅ manifest verified"; \
	  rm -rf "$$tmp"; \
	'

# =============================================================================
# Full Local E2E Lane (No Copy/Paste Disasters)
# =============================================================================

.PHONY: e2e-local e2e-start e2e-stop e2e-wait

e2e-start: up-local
	@true

e2e-wait: ready-local
	@true

e2e-stop: down-local
	@true

e2e-local: test e2e-start e2e-wait test-integration evidence evidence-verify e2e-stop
	@echo "✅ e2e-local complete"

# =============================================================================
# CI / Guards
# =============================================================================

.PHONY: ci-tools guard-no-8000 guard-no-pytest-detection ci build

ci-tools:
	@command -v rg >/dev/null || (echo "❌ rg missing" && exit 1)
	@command -v curl >/dev/null || (echo "❌ curl missing" && exit 1)
	@command -v sqlite3 >/dev/null || (echo "❌ sqlite3 missing" && exit 1)
	@command -v zip >/dev/null || (echo "❌ zip missing" && exit 1)
	@command -v go >/dev/null || (echo "❌ go missing" && exit 1)
	@echo "✅ CI tools present"

guard-no-8000:
	@rg -n "127\.0\.0\.1:8000|:8000\b" scripts api tests *.py *.sh 2>/dev/null && \
	 (echo "❌ Hardcoded :8000 found. Use BASE_URL." && exit 1) || \
	 echo "✅ No hardcoded :8000 found"

guard-no-pytest-detection:
	@rg -n "_running_under_pytest|PYTEST_CURRENT_TEST|sys\.modules\['pytest'\]" api/main.py >/dev/null && \
	 (echo "❌ Pytest-detection found in api/main.py. Remove test hacks." && exit 1) || \
	 echo "✅ No pytest-detection in api/main.py"

build:
	cd supervisor-sidecar && go build ./...

ci: ci-tools guard-no-8000 guard-no-pytest-detection test-sanity build

# =============================================================================
# Docker
# =============================================================================

.PHONY: docker-build docker-push docker-release docker-build-local docker-run docker-shell

docker-build:
	docker build -t $(CORE_IMAGE):$(VERSION) -t $(CORE_IMAGE):latest .
	docker build -t $(SIDE_IMAGE):$(VERSION) -t $(SIDE_IMAGE):latest supervisor-sidecar

docker-push:
	docker push $(CORE_IMAGE):$(VERSION)
	docker push $(CORE_IMAGE):latest
	docker push $(SIDE_IMAGE):$(VERSION)
	docker push $(SIDE_IMAGE):latest

docker-release: docker-build docker-push

docker-build-local:
	docker build -t frostgate-core:local .

docker-run:
	docker run --rm -p 8080:8080 \
	  -e FG_ENV=dev \
	  -e FG_ENFORCEMENT_MODE=observe \
	  -e FG_AUTH_ENABLED=1 \
	  -e FG_API_KEY="$(FG_API_KEY)" \
	  -e FG_SQLITE_PATH=/state/frostgate.db \
	  frostgate-core:local

docker-shell:
	docker run --rm -it frostgate-core:local /bin/bash

# =============================================================================
# Tenant Tools
# =============================================================================

.PHONY: tenant-add tenant-list

tenant-add:
	@test -n "$(TENANT_ID)" || (echo "TENANT_ID is required" && exit 1)
	"$(PYTHON)" -m tools.tenants add "$(TENANT_ID)"

tenant-list:
	"$(PYTHON)" -m tools.tenants list

# =============================================================================
# Build/Deploy Scripts
# =============================================================================

.PHONY: build-dev build-prod deploy-dev deploy-prod

build-dev:
	ENVIRONMENT=dev PUSH_IMAGE=0 scripts/build.sh

build-prod:
	ENVIRONMENT=prod scripts/build.sh

deploy-dev:
	ENVIRONMENT=dev scripts/deploy_dev.sh

deploy-prod:
	ENVIRONMENT=prod VERSION=$(VERSION) scripts/deploy_prod.sh
