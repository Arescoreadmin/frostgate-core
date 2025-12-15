#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-frostgate-dev}"
K8S_NAMESPACE="${K8S_NAMESPACE:-frostgate-dev}"
KIND_CONFIG="${KIND_CONFIG:-deploy/k8s/dev/kind-config.yaml}"

echo "[k8s-dev] Using cluster: ${CLUSTER_NAME}"
echo "[k8s-dev] Using namespace: ${K8S_NAMESPACE}"

if ! command -v kind >/dev/null 2>&1; then
  echo "[k8s-dev] ERROR: kind not installed"
  exit 1
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "[k8s-dev] ERROR: kubectl not installed"
  exit 1
fi

if ! kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}\$"; then
  echo "[k8s-dev] Creating kind cluster ${CLUSTER_NAME}..."
  kind create cluster --name "${CLUSTER_NAME}" --config "${KIND_CONFIG}"
else
  echo "[k8s-dev] Cluster ${CLUSTER_NAME} already exists."
fi

if ! kubectl get ns "${K8S_NAMESPACE}" >/dev/null 2>&1; then
  echo "[k8s-dev] Creating namespace ${K8S_NAMESPACE}..."
  kubectl create namespace "${K8S_NAMESPACE}"
else
  echo "[k8s-dev] Namespace ${K8S_NAMESPACE} already exists."
fi

echo "[k8s-dev] Cluster & namespace ready."
