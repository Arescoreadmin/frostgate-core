#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

source .venv/bin/activate
export FG_AUTH_ENABLED="${FG_AUTH_ENABLED:-true}"
export FG_API_KEY="${FG_API_KEY:-supersecret}"
export FG_STATE_DIR="${FG_STATE_DIR:-$PWD/state}"
export FG_SQLITE_PATH="${FG_SQLITE_PATH:-$FG_STATE_DIR/frostgate.db}"
mkdir -p "$FG_STATE_DIR"

# free port if needed
if ss -ltnp | grep -q ':8000'; then
  echo "[demo] port 8000 in use; kill it or change FG_PORT" >&2
fi

export FG_HOST="${FG_HOST:-127.0.0.1}"
export FG_PORT="${FG_PORT:-8000}"
export FG_URL="http://$FG_HOST:$FG_PORT"

echo "[demo] starting core at $FG_URL"
uvicorn api.main:app --host "$FG_HOST" --port "$FG_PORT" --reload &
UV_PID=$!

cleanup() { kill "$UV_PID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

./scripts/wait_core_ready.sh "$FG_URL/health/ready"

echo "[demo] seeding"
BASE_URL="$FG_URL" ./scripts/seed_demo_decisions.sh

echo "[demo] stats"
curl -fsS "$FG_URL/stats" -H "X-API-Key: $FG_API_KEY" | python -m json.tool
