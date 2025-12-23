#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo ".env missing"
  exit 1
fi

FG_AGENT_KEY="$(grep ^FG_AGENT_KEY= .env | cut -d= -f2-)"
export FG_AGENT_KEY
export FG_CORE_URL="${FG_CORE_URL:-http://localhost:18080}"
export FG_AGENT_QUEUE_DIR="${FG_AGENT_QUEUE_DIR:-/var/lib/frostgate/agent_queue}"
export FG_AGENT_SOURCE="${FG_AGENT_SOURCE:-edge1}"
export FG_AGENT_TENANT_ID="${FG_AGENT_TENANT_ID:-t1}"

python -m agent.agent_main
