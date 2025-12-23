#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"

echo "== FrostGate Core audit =="
echo "Root: $ROOT"
echo

echo "## rate limit import drift (ratelimit vs rate_limit)"
rg -n --hidden --glob '!**/.venv/**' --glob '!**/.git/**' \
  'from api\.(ratelimit|rate_limit) import rate_limit_guard' "$ROOT" || true
echo

echo "## TelemetryInput duplicates"
rg -n --hidden --glob '!**/.venv/**' --glob '!**/.git/**' \
  '^class\s+TelemetryInput\b' "$ROOT/api" || true
echo

echo "## LegacyTelemetryInput duplicates"
rg -n --hidden --glob '!**/.venv/**' --glob '!**/.git/**' \
  '^class\s+LegacyTelemetryInput\b' "$ROOT/api" || true
echo

echo "## DecisionRecord column mismatches"
rg -n --hidden --glob '!**/.venv/**' --glob '!**/.git/**' \
  'rules_triggered_json|rules_triggered=' "$ROOT/api" || true
echo

echo "## Router includes"
rg -n --hidden --glob '!**/.venv/**' --glob '!**/.git/**' \
  'include_router\(' "$ROOT/api/main.py" || true
echo

echo "## Compile check"
python -m compileall -q "$ROOT/api" "$ROOT/engine" || true
echo
echo "== Audit complete =="
