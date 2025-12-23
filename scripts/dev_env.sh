#!/usr/bin/env bash
set -euo pipefail

export FG_URL="${FG_URL:-http://localhost:18080}"
export FG_KEY="$(docker compose exec -T frostgate-core sh -lc 'cd /app && PYTHONPATH=/app python scripts/mint_api_key.py' | tr -d "\r\n")"

printf 'FG_URL=[%s]\n' "$FG_URL"
printf 'FG_KEY=[%s]\n' "$FG_KEY"
