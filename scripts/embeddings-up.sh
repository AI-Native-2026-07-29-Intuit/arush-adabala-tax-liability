#!/usr/bin/env bash
# scripts/embeddings-up.sh - bring the self-hosted embeddings service up on a local k3d cluster
# and prove it, for the W6 D4 Task 3 check:
#
#   kubectl -n taxcalc-dev get deploy embeddings   ->   1/1 Available
#
# This is the WHOLE path, because four separate things stand between a clean machine and that
# one line, and each of them fails in a way that names something other than its cause. They are
# documented at the step that handles them:
#
#   1. the model weights TEI cannot download for itself   (FETCH THE MODEL)
#   2. k3s refusing to start on a cgroup v1 host          (CLUSTER)
#   3. containerd not trusting the intercepting proxy's CA (PRELOAD IMAGES)
#   4. a default StorageClass stealing the PVC            (STORAGE)
#
# Idempotent: every step checks before acting, so re-running is a fast no-op that re-applies the
# manifests. Roughly 5 minutes on a cold machine, almost all of it the 1.3GB model download.
#
# Usage:
#   scripts/embeddings-up.sh            # bring it up and verify
#   scripts/embeddings-up.sh --smoke    # also POST /embed and assert 1024 floats come back
#   MODELS_DIR=/other/path scripts/embeddings-up.sh
#
# Teardown:  k3d cluster delete taxcalc     (the model files survive - see RECLAIM below)

set -euo pipefail

# Rancher Desktop's ~/.rd/bin/kubectl is a kuberlr shim that tries to download a client matching
# the cluster and 404s on Apple Silicon, turning every kubectl call into a failure with an
# unrelated message. This picks one that actually runs.
# shellcheck source=lib/kube-preflight.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/kube-preflight.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="${K3D_CLUSTER:-taxcalc}"
NAMESPACE="taxcalc-dev"
MODELS_DIR="${MODELS_DIR:-$HOME/tei-models}"
MODEL="bge-large-en-v1.5"
SMOKE_PORT="${SMOKE_PORT:-8088}"

# k3s v1.28 is the last line that runs on a cgroup v1 host - see CLUSTER below.
K3S_IMAGE="${K3S_IMAGE:-rancher/k3s:v1.28.15-k3s1}"
TEI_IMAGE="ghcr.io/huggingface/text-embeddings-inference:cpu-1.5"
PAUSE_IMAGE="rancher/mirrored-pause:3.6"
# k3s deploys this itself. It is in the preload list because when its pull fails, EVERY later
# kubectl command prints several lines of `Couldn't get resource list ... metrics.k8s.io/v1beta1`
# to stderr - the APIService exists with no endpoints behind it, so discovery keeps trying. That
# noise looks like an error in whatever command printed it, and buries the one line this script
# exists to show. Preloading it is a one-line fix for the cause; filtering the output would only
# hide it. Re-derive the tag with:
#   kubectl -n kube-system get deploy metrics-server -o jsonpath='{..image}'
METRICS_IMAGE="rancher/mirrored-metrics-server:v0.7.2"

step() { echo; echo "==> $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. FETCH THE MODEL
#
# The Deployment passes `--model-id /models/bge-large-en-v1.5`, a DIRECTORY, and TEI then loads
# from disk without ever contacting huggingface.co. That is deliberate: TEI's bundled hf-hub
# 0.3.2 disables reqwest's redirect following and re-implements it by handing the raw `Location`
# header to `client.get(...)`, which needs an ABSOLUTE url - so behind a TLS-intercepting proxy
# that rewrites that redirect to a relative path it dies with `relative URL without a base`,
# having fetched nothing. 0.4.3 carries identical code and `HF_ENDPOINT` is not an escape hatch.
#
# curl follows the redirect correctly, which is why fetching out of band works where the pod's
# own downloader cannot. onnx/model.onnx and NOT model.safetensors: the CPU build uses the ONNX
# Runtime backend and, given only safetensors, reaches `Starting model backend` and dies naming a
# file that does not exist - which reads like a packaging fault rather than a wrong artefact.
step "model files in ${MODELS_DIR}/${MODEL}"
DEST="${MODELS_DIR}/${MODEL}"
BASE="https://huggingface.co/BAAI/${MODEL}/resolve/main"
mkdir -p "${DEST}/1_Pooling" "${DEST}/onnx"

fetch() { # fetch <relative-path>
    local rel="$1"
    if [ -s "${DEST}/${rel}" ]; then
        echo "    have ${rel}"
        return
    fi
    echo "    fetching ${rel}"
    curl -fsSL -o "${DEST}/${rel}" "${BASE}/${rel}" \
        || fail "could not fetch ${rel}. If this machine has no egress to huggingface.co, copy
      ${DEST} from a machine that does - the pod cannot download it for itself."
}

for f in config.json tokenizer.json tokenizer_config.json special_tokens_map.json \
         sentence_bert_config.json config_sentence_transformers.json vocab.txt; do
    fetch "$f"
done
fetch "1_Pooling/config.json"
fetch "onnx/model.onnx"   # ~1.3GB, the slow one

# ---------------------------------------------------------------- 2. CLUSTER
#
# `--volume $MODELS_DIR:/tei-models@all` is what makes the weights reachable from inside the
# cluster, and it can only be set at CREATE time - a cluster that already exists without it
# cannot have it added, which is why the check below is explicit rather than a silent reuse.
#
# K3S_IMAGE is pinned to a v1.28 build on purpose. Recent k3s refuses to start on a host whose
# container VM still runs cgroup v1 - `failed to validate kubelet configuration, error: kubelet
# is configured to not run on a host using cgroup v1` - and the symptom is not that message but a
# node that never registers while `kubectl` reports `connection refused` for ten minutes. Unset
# K3S_IMAGE to let k3d pick its own default on a cgroup v2 host.
step "k3d cluster ${CLUSTER}"
if k3d cluster list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "${CLUSTER}"; then
    if docker inspect "k3d-${CLUSTER}-server-0" \
        --format '{{range .Mounts}}{{.Destination}} {{end}}' 2>/dev/null | grep -q '/tei-models'; then
        echo "    exists, with the model volume mounted"
    else
        fail "cluster ${CLUSTER} exists but has no /tei-models mount, and a mount cannot be added
      to a running k3d cluster. Recreate it:  k3d cluster delete ${CLUSTER} && $0"
    fi
else
    echo "    creating (k3s image ${K3S_IMAGE})"
    k3d cluster create "${CLUSTER}" \
        --image "${K3S_IMAGE}" \
        --servers 1 --agents 0 \
        --volume "${MODELS_DIR}:/tei-models@all" \
        --k3s-arg "--disable=traefik@server:0" >/dev/null
fi
k3d kubeconfig merge "${CLUSTER}" --kubeconfig-merge-default --kubeconfig-switch-context >/dev/null

echo "    waiting for the API server"
for _ in $(seq 1 60); do
    kubectl get nodes >/dev/null 2>&1 && break
    sleep 5
done
kubectl get nodes >/dev/null 2>&1 || fail "API server never became reachable"

# ---------------------------------------------------------------- 3. PRELOAD IMAGES
#
# Same reason as scripts/observability-preload-images.sh: the host's Docker daemon trusts the
# interception CA (it is in the macOS System keychain) and containerd inside the k3d node does
# not, so an in-cluster pull dies with `x509: certificate signed by unknown authority`. Pulling on
# the host and importing sidesteps the in-cluster pull entirely.
#
# The pause image is in this list for a reason that costs an hour to work out otherwise: without
# it the failure is `FailedCreatePodSandBox ... failed to get sandbox image`, which points at the
# pod's own image and not at the sandbox's.
#
# --platform linux/amd64 because there is NO arm64 build of TEI at any tag (checked cpu-1.5, 1.5,
# cpu-1.6, cpu-1.7, cpu-latest). On Apple Silicon it therefore runs under emulation - which works,
# and is why the manifest's startupProbe is generous.
step "preloading images into the cluster"
for image in "${PAUSE_IMAGE}" "${TEI_IMAGE}" "${METRICS_IMAGE}"; do
    if docker image inspect "${image}" >/dev/null 2>&1; then
        echo "    have ${image}"
    else
        echo "    pulling ${image}"
        docker pull --platform linux/amd64 "${image}" >/dev/null \
            || fail "could not pull ${image} on the host"
    fi
done
k3d image import -c "${CLUSTER}" "${PAUSE_IMAGE}" "${TEI_IMAGE}" "${METRICS_IMAGE}" >/dev/null 2>&1 \
    || fail "k3d image import failed"
echo "    imported"

# The metrics-server pod is already in ImagePullBackOff by now on a fresh cluster; kubelet's
# backoff can be a couple of minutes, and a restart takes the image that now exists immediately.
kubectl -n kube-system rollout restart deploy/metrics-server >/dev/null 2>&1 || true

# ---------------------------------------------------------------- 4. STORAGE
#
# k3s ships `local-path` as the DEFAULT StorageClass, and a PVC naming no class gets that default
# stamped on by admission, is dynamically provisioned, and binds to a fresh EMPTY directory -
# never to the PersistentVolume holding the models. The pod then starts, mounts nothing useful,
# and TEI dies naming a missing model file, which looks like a download problem and is a binding
# problem. Turning the default off lets the claim match the `storageClassName: ""` volume in
# manifests/dev/70-embeddings-models.localpv.yaml.
#
# RECLAIM: that PV is `persistentVolumeReclaimPolicy: Retain`, so deleting the claim - or the
# whole cluster - never deletes the developer's 1.3GB of model files.
step "storage"
kubectl patch storageclass local-path \
    -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}' \
    >/dev/null 2>&1 || true
echo "    local-path is no longer the default StorageClass"

kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -f "${ROOT}/manifests/dev/70-embeddings-models.localpv.yaml" >/dev/null
echo "    PersistentVolume applied"

# ---------------------------------------------------------------- 5. THE COMMITTED MANIFEST
#
# Unmodified, which is the point of the whole exercise: everything environment-specific above is
# provisioning, and what gets deployed here is the same file a real cluster would get.
step "applying manifests/70-embeddings.deployment.yaml"
kubectl apply -f "${ROOT}/manifests/70-embeddings.deployment.yaml" >/dev/null
kubectl -n "${NAMESPACE}" get pvc embeddings-models \
    -o jsonpath='{.status.phase} -> {.spec.volumeName}{"\n"}' | sed 's/^/    claim: /'

# Emulated amd64 loading a 1.3GB ONNX graph: minutes, not seconds. The manifest's startupProbe
# allows 300s and this allows a little more, so a slow machine reports a timeout here rather than
# a CrashLoopBackOff that looks like a broken image.
step "waiting for rollout (emulated amd64 - this is the slow part)"
kubectl -n "${NAMESPACE}" rollout status deploy/embeddings --timeout=420s \
    || fail "the Deployment never became Available. Look at:
      kubectl -n ${NAMESPACE} describe pod -l app=embeddings
      kubectl -n ${NAMESPACE} logs deploy/embeddings"

# ---------------------------------------------------------------- 6. THE CHECK
step "kubectl -n ${NAMESPACE} get deploy embeddings"
kubectl -n "${NAMESPACE}" get deploy embeddings
AVAILABLE="$(kubectl -n "${NAMESPACE}" get deploy embeddings \
    -o jsonpath='{.status.conditions[?(@.type=="Available")].status}')"
[ "${AVAILABLE}" = "True" ] || fail "Available=${AVAILABLE}"

if [ "${1:-}" = "--smoke" ]; then
    # Proves the model is actually serving, not merely that the probes pass: /health answers
    # before a single embedding has ever been produced.
    step "POST /embed"
    kubectl -n "${NAMESPACE}" port-forward svc/embeddings "${SMOKE_PORT}:80" >/tmp/pf-embeddings.log 2>&1 &
    PF_PID=$!
    trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
    sleep 8
    DIMS="$(curl -fsS -X POST "http://localhost:${SMOKE_PORT}/embed" \
        -H 'Content-Type: application/json' \
        -d '{"inputs":"AGI 120000, single filer, standard deduction"}' \
        | python3 -c 'import json,sys; print(len(json.load(sys.stdin)[0]))')"
    [ "${DIMS}" = "1024" ] || fail "/embed returned ${DIMS} dimensions, expected 1024 - the
      taxpayer_embeddings.embedding column is vector(1024), so a different geometry cannot be
      stored at all"
    echo "    /embed returned a ${DIMS}-dimension vector"
    echo
    echo "    The application's own live test runs against this, unmodified:"
    echo "      ./gradlew test --tests '*EmbeddingsClientLiveIT'"
    echo "    (it defaults to http://localhost:${SMOKE_PORT}/embed; keep the port-forward up)"
fi

echo
echo "PASS: deploy/embeddings is 1/1 Available in ${NAMESPACE}, serving ${MODEL} from disk."
