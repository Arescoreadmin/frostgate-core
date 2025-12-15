#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export ENVIRONMENT="${ENVIRONMENT:-dev}"
export VERSION="${VERSION:-dev-local}"
export PUSH_IMAGE="${PUSH_IMAGE:-0}"
export K8S_NAMESPACE="${K8S_NAMESPACE:-frostgate-dev}"
export CLUSTER_NAME="${CLUSTER_NAME:-frostgate-dev}"

IMAGE_TAG="registry.internal/frostgate/frostgate-core:${ENVIRONMENT}-${VERSION}"

echo "[dev-deploy] Ensuring dev cluster..."
"${SCRIPT_DIR}/k8s-dev-cluster.sh"

echo "[dev-deploy] Building image ${IMAGE_TAG}..."
ENVIRONMENT="${ENVIRONMENT}" VERSION="${VERSION}" PUSH_IMAGE="${PUSH_IMAGE}" \
  "${SCRIPT_DIR}/build.sh"

echo "[dev-deploy] Loading image into kind cluster ${CLUSTER_NAME}..."
kind load docker-image "${IMAGE_TAG}" --name "${CLUSTER_NAME}"

echo "[dev-deploy] Applying k8s manifests..."
kubectl -n "${K8S_NAMESPACE}" apply \
  -f "${PROJECT_ROOT}/deploy/k8s/dev/deployment.yaml" \
  -f "${PROJECT_ROOT}/deploy/k8s/dev/service.yaml"

echo "[dev-deploy] Waiting for frostgate-core rollout..."
kubectl -n "${K8S_NAMESPACE}" rollout status deploy/frostgate-core

echo "[dev-deploy] Current pods:"
kubectl -n "${K8S_NAMESPACE}" get pods -l app=frostgate-core -o wide

echo "[dev-deploy] Done."
