#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

ENVIRONMENT="${ENVIRONMENT:-dev}"  # tests run under dev config by default
ENV_FILE="$PROJECT_ROOT/.env.$ENVIRONMENT"

log "Loading env from $ENV_FILE"
load_env_file "$ENV_FILE"

ensure_venv
source "$VENV_PATH/bin/activate"

log "Running tests (pytest -q)..."
PYTHONPATH="$PROJECT_ROOT" "$VENV_PATH/bin/python" -m pytest -q
log "Tests passed."
