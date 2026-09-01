#!/usr/bin/env bash
# scripts/k8s-up.sh - one-shot bring-up for the k3d cluster + manifests.
# Idempotent: re-running just re-applies; no state lost.
set -euo pipefail

CLUSTER="taxcalc"
NAMESPACE="taxcalc-dev"

# The image tag is read straight out of the Deployment manifest rather than
# hardcoded here - a hardcoded default silently drifts every time the manifest's
# tag is bumped, and then `k3d image import` imports an image the Deployment
# never asks for (imagePullPolicy: IfNotPresent then falls through to a registry
# pull for an image that was never pushed -> ImagePullBackOff).
TAG="${TAG:-$(sed -n 's|.*image: uptimecrew/taxcalc-api:\([^ ]*\).*|\1|p' \
  manifests/10-taxcalc-api.deployment.yaml | head -1)}"
IMAGE="uptimecrew/taxcalc-api:${TAG}"

# Local-dev Postgres password. Postgres and taxcalc-api deliberately read the
# SAME secret (see manifests/05-dev-dependencies.yaml) so they can never drift
# apart - but Postgres only honours POSTGRES_PASSWORD at initdb time, so the
# secret has to be seeded BEFORE its first boot and the pod recycled whenever
# it changes. Both are handled below.
DEV_PG_PASSWORD="${DEV_PG_PASSWORD:-devpass}"

echo "Using image ${IMAGE}"

# 1. Cluster (create only if missing).
#    K3S_IMAGE is an escape hatch, empty by default so k3d picks its own
#    current default. Recent k3s requires cgroup v2 and dies at boot with
#    "kubelet is configured to not run on a host using cgroup v1" on hosts
#    whose container VM still runs v1 (some Rancher Desktop / Lima setups);
#    there, export K3S_IMAGE=rancher/k3s:v1.28.15-k3s1 to get a v1-tolerant
#    build rather than pinning an old k3s for everyone.
if ! k3d cluster list | awk 'NR>1 { print $1 }' | grep -qx "${CLUSTER}"; then
  echo "Creating k3d cluster ${CLUSTER}..."
  k3d cluster create "${CLUSTER}" \
    ${K3S_IMAGE:+--image "${K3S_IMAGE}"} \
    --servers 1 --agents 2 \
    --port "8080:80@loadbalancer" \
    --k3s-arg "--disable=traefik@server:0"
fi
k3d kubeconfig merge "${CLUSTER}" --kubeconfig-merge-default --kubeconfig-switch-context >/dev/null

# 2. Ingress controller. k3d ships Traefik, which we disabled above, so
#    `ingressClassName: nginx` in 60-taxcalc-api.ingress.yaml matches nothing
#    until a real NGINX controller exists - without this the Ingress object
#    applies cleanly but never serves a byte, and k8s-smoke.sh's checks fail.
if ! kubectl get deploy ingress-nginx-controller -n ingress-nginx >/dev/null 2>&1; then
  echo "Deploying ingress-nginx..."
  curl -sL https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/baremetal/deploy.yaml \
    -o /tmp/ingress-nginx-baremetal.yaml
  # k3d's `--port "8080:80@loadbalancer"` is a static node-IP:port proxy, not
  # Service-aware: the controller only sees that traffic if it binds the node's
  # own port 80, which the upstream manifest doesn't do by default.
  sed -i.bak 's/^      dnsPolicy: ClusterFirst$/      dnsPolicy: ClusterFirstWithHostNet\n      hostNetwork: true/' \
    /tmp/ingress-nginx-baremetal.yaml
  kubectl apply -f /tmp/ingress-nginx-baremetal.yaml
  # Its ValidatingWebhookConfiguration has no ready endpoints until the
  # cert-gen Jobs finish, and until then every Ingress apply 500s against it.
  # Nothing here relies on Ingress admission validation.
  kubectl delete validatingwebhookconfiguration ingress-nginx-admission --ignore-not-found
  kubectl -n ingress-nginx wait --for=condition=available --timeout=180s deploy/ingress-nginx-controller
fi

# 3. Import the locally-built image into the cluster's containerd cache
#    so nodes don't need a registry round-trip. `k3d image import` can race its
#    own shared-volume write on a cold cluster and report success while having
#    loaded nothing, so verify per-node instead of trusting the exit code.
echo "Importing ${IMAGE} into ${CLUSTER}..."
nodes=$(k3d node list --no-headers | awk -v c="k3d-${CLUSTER}-" '$1 ~ "^"c && $2 != "loadbalancer" { print $1 }')
for attempt in 1 2 3; do
  k3d image import "${IMAGE}" -c "${CLUSTER}" || true
  missing=0
  for node in ${nodes}; do
    docker exec "${node}" crictl inspecti "${IMAGE}" >/dev/null 2>&1 || missing=1
  done
  [ "${missing}" -eq 0 ] && break
  if [ "${attempt}" -eq 3 ]; then
    echo "ERROR: ${IMAGE} missing from at least one node after 3 import attempts." >&2
    echo "       Build it first: docker build -t ${IMAGE} ." >&2
    exit 1
  fi
  echo "Import incomplete, retrying in 5s..."
  sleep 5
done

# 4. Namespace + the local-dev Secret, BEFORE the workloads that read them.
#    manifests/40-taxcalc-api.secret.yaml ships a placeholder that must never be
#    a real password, so the real local value is injected here instead. Doing it
#    before step 5 means Postgres initdbs with the right password first time.
kubectl apply -f manifests/00-namespace.yaml
kubectl create secret generic taxcalc-api-secrets \
  -n "${NAMESPACE}" \
  --from-literal=SPRING_DATASOURCE_PASSWORD="${DEV_PG_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

# 5. Apply the manifest tree in lexical order (00-, 05-, 10-, ...).
echo "Applying manifests/..."
kubectl apply -f manifests/

# 6. `kubectl apply -f manifests/` in step 5 re-applied the committed
#    placeholder over the real Secret. Put the local value back, then recycle
#    the two Deployments that consume it - Postgres has no PVC, so recycling it
#    triggers a fresh initdb against the restored password and keeps the two
#    sides in sync (this exact drift is what causes
#    `FATAL: password authentication failed for user "taxcalc_dev"`).
kubectl create secret generic taxcalc-api-secrets \
  -n "${NAMESPACE}" \
  --from-literal=SPRING_DATASOURCE_PASSWORD="${DEV_PG_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl rollout restart deploy/postgres deploy/taxcalc-api -n "${NAMESPACE}"
kubectl rollout status deploy/postgres -n "${NAMESPACE}" --timeout=3m

# 7. Block until the app rollout completes (or fail loudly).
kubectl rollout status deploy/taxcalc-api \
  -n "${NAMESPACE}" --timeout=5m

echo ""
echo "Cluster ready. Reach the service through the Ingress:"
echo "  curl -H 'Host: taxcalc.dev.uptimecrew.internal' http://localhost:8080/actuator/health/readiness"
