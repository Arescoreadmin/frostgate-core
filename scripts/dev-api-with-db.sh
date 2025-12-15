#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -d ".venv" ]; then
  echo "Missing .venv – create with: python3 -m venv .venv" >&2
  exit 1
fi

source .venv/bin/activate

export FG_DB_URL="${FG_DB_URL:-sqlite:///./frostgate_decisions.db}"
export FG_API_KEY="${FG_API_KEY:-supersecret}"

echo "[dev-api] DB: ${FG_DB_URL}"
echo "[dev-api] Starting frostgate-core dev API on port 18080"

uvicorn api.main:app \
  --host 0.0.0.0 \
  --port 18080 \
  --reload
