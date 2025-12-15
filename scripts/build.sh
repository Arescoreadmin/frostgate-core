#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Sensible defaults for local dev
: "${ENVIRONMENT:=dev}"
: "${VERSION:=dev-local}"
: "${PUSH_IMAGE:=0}"   # <– default: DO NOT push

log "Building image for ENVIRONMENT=$ENVIRONMENT VERSION=$VERSION"
log "Image tag: $IMAGE_TAG"

docker build \
  --build-arg ENVIRONMENT="$ENVIRONMENT" \
  -t "$IMAGE_TAG" \
  "$PROJECT_ROOT"

if [ "$PUSH_IMAGE" -eq 1 ]; then
  log "Pushing image $IMAGE_TAG"
  docker push "$IMAGE_TAG"
else
  log "Skipping push (PUSH_IMAGE=$PUSH_IMAGE)"
fi
