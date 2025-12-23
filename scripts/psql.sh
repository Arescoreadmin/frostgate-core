#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   scripts/psql.sh -c "\d api_keys"
#   scripts/psql.sh frostgate -c "select 1"
#
# If first arg doesn't start with '-', treat it as DB name override.
DB_OVERRIDE=""
if [[ "${1:-}" != "" && "${1:-}" != "-"* ]]; then
  DB_OVERRIDE="$1"
  shift
fi

PGUSER="$(docker compose exec -T postgres sh -lc 'printf "%s" "$POSTGRES_USER"')"
PGDB="$(docker compose exec -T postgres sh -lc 'printf "%s" "$POSTGRES_DB"')"

if [[ -n "$DB_OVERRIDE" ]]; then
  PGDB="$DB_OVERRIDE"
fi

# IMPORTANT: use "$@" not $* so quoting stays intact.
docker compose exec -T postgres psql -U "$PGUSER" -d "$PGDB" "$@"
