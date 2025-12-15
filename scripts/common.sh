#!/usr/bin/env bash
set -euo pipefail

# Registry & image naming
REGISTRY="${REGISTRY:-registry.internal/frostgate}"   # change for your setup
IMAGE_NAME="${IMAGE_NAME:-frostgate-core}"

# Environment: dev | prod
ENVIRONMENT="${ENVIRONMENT:-dev}"

# Version/tag
GIT_SHA="$(git rev-parse --short HEAD)"
VERSION="${VERSION:-$GIT_SHA}"

# Full tag, namespaced by environment for safety
IMAGE_TAG="$REGISTRY/$IMAGE_NAME:$ENVIRONMENT-$VERSION"

# Paths
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PATH="${VENV_PATH:-$PROJECT_ROOT/.venv}"

function log() {
  echo "[$(date --iso-8601=seconds)] $*"
}

function ensure_venv() {
  if [ ! -d "$VENV_PATH" ]; then
    log "Python venv not found at $VENV_PATH"
    log "Create it with: python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
  fi
}

function load_env_file() {
  local env_file="$1"
  if [ -f "$env_file" ]; then
    # shellcheck disable=SC2046
    export $(grep -v '^#' "$env_file" | xargs -0 -I {} echo {} 2>/dev/null || true)
  else
    log "WARN: env file $env_file not found, continuing without it"
  fi
}
