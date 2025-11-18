#!/usr/bin/env bash
set -e

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -d ".venv" ]; then
  echo "Missing .venv – create it with: python3 -m venv .venv"
  exit 1
fi

source .venv/bin/activate

export FG_CORE_BASE_URL="${FG_CORE_BASE_URL:-http://127.0.0.1:8080}"
export FG_CHAOS_COUNT="${FG_CHAOS_COUNT:-10}"

echo "Running chaos-monkey against FG_CORE_BASE_URL=${FG_CORE_BASE_URL} (count=${FG_CHAOS_COUNT})"
python jobs/chaos-monkey/job.py
