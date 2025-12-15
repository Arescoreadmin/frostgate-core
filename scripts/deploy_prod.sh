#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/common.sh"

ENVIRONMENT=prod
ENV_FILE="$PROJECT_ROOT/.env.prod"
: "${BUILD_BEFORE_DEPLOY:=1}"

if [ -z "${VERSION:-}" ]; then
  echo "ERROR: VERSION is required, e.g. VERSION=v0.1.0" >&2
  exit 1
fi

load_env_file "$ENV_FILE"

log "Deploying PROD with image: $IMAGE_TAG"

if [ "$BUILD_BEFORE_DEPLOY" -eq 1 ]; then
  log "BUILD_BEFORE_DEPLOY=1, invoking build.sh"
  PUSH_IMAGE="${PUSH_IMAGE:-1}" "$SCRIPT_DIR/build.sh"
else
  log "BUILD_BEFORE_DEPLOY=0, skipping image build"
fi

if command -v kubectl >/dev/null 2>&1; then
  log "Applying K8s manifests for PROD"
  kubectl apply -k "$PROJECT_ROOT/deploy/k8s/prod"
else
  log "kubectl not found, skipping cluster deploy (probably local dev)"
fi

log "PROD deploy completed."
