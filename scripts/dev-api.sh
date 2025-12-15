#!/usr/bin/env bash
set -euo pipefail

: "${FG_API_HOST:?FG_API_HOST not set}"
: "${FG_API_PORT:?FG_API_PORT not set}"

echo "Starting frostgate-core dev API on port ${FG_API_PORT}"

exec uvicorn api.main:app \
  --host "${FG_API_HOST}" \
  --port "${FG_API_PORT}" \
  --reload
