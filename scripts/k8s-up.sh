#!/usr/bin/env bash
# scripts/k8s-up.sh - one-shot bring-up for the k3d cluster + manifests.
# Idempotent: re-running just re-applies; no state lost.
set -euo pipefail

CLUSTER="taxcalc"
NAMESPACE="taxcalc-dev"
TAG="${TAG:-0.1.0}"
IMAGE="uptimecrew/taxcalc-api:${TAG}"

# 1. Cluster (create only if missing).
if ! k3d cluster list | awk 'NR>1 { print $1 }' | grep -qx "${CLUSTER}"; then
  echo "Creating k3d cluster ${CLUSTER}..."
  k3d cluster create "${CLUSTER}" \
    --servers 1 --agents 2 \
    --port "8080:80@loadbalancer" \
    --k3s-arg "--disable=traefik@server:0"
fi
k3d kubeconfig merge "${CLUSTER}" --kubeconfig-merge-default --kubeconfig-switch-context >/dev/null

# 2. Import the locally-built image into the cluster's containerd cache
#    so nodes don't need a registry round-trip.
echo "Importing ${IMAGE} into ${CLUSTER}..."
k3d image import "${IMAGE}" -c "${CLUSTER}"

# 3. Apply the manifest tree in lexical order (00-, 05-, 10-, ...).
echo "Applying manifests/..."
kubectl apply -f manifests/

# 4. Block until the rollout completes (or fail loudly).
kubectl rollout status deploy/taxcalc-api \
  -n "${NAMESPACE}" --timeout=5m

echo ""
echo "Cluster ready. Reach the service through the Ingress:"
echo "  curl -H 'Host: taxcalc.dev.uptimecrew.internal' http://localhost:8080/actuator/health/readiness"
