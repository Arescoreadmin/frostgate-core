#!/usr/bin/env bash
set -e

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v go >/dev/null 2>&1; then
  echo "Go is not installed or not in PATH. Install Go 1.22+."
  exit 1
fi

export FG_CORE_BASE_URL="${FG_CORE_BASE_URL:-http://127.0.0.1:8080}"
export SUPERVISOR_LISTEN_ADDR="${SUPERVISOR_LISTEN_ADDR:-:9090}"

echo "Starting supervisor-sidecar against FG_CORE_BASE_URL=${FG_CORE_BASE_URL}"

cd supervisor-sidecar
go run .
