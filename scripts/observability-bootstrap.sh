#!/usr/bin/env bash
# scripts/observability-bootstrap.sh - install the PLG-T stack into the `monitoring` namespace.
#
# Run ONCE per cluster, before scripts/observability-apply.sh. Everything here is cluster
# infrastructure (Prometheus, Grafana, Loki, Alloy, Tempo, OTel Collector); the application's own
# observability objects live in manifests/observability/ and are applied separately, so this
# script never has to be re-run after an app change.
#
# Idempotent: `helm upgrade --install` re-converges an existing release instead of failing.
set -euo pipefail

# A working kubectl is not a given here - see the file for the Rancher Desktop shim this catches.
# shellcheck source=lib/kube-preflight.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/kube-preflight.sh"

NS="${OBS_NAMESPACE:-monitoring}"
VALUES="$(cd "$(dirname "${BASH_SOURCE[0]}")/../manifests/observability/helm" && pwd)"

# An optional second values file per chart, layered on top of the base one. Set
# OBS_VALUES_OVERLAY=ci to trim the stack to what a 2-vCPU CI runner can actually hold (see
# manifests/observability/helm/ci/). Unset locally, so a developer gets the full stack.
OVERLAY="${OBS_VALUES_OVERLAY:-}"
overlay_arg() {  # overlay_arg <chart-values-filename>
  [ -n "${OVERLAY}" ] && [ -f "${VALUES}/${OVERLAY}/$1" ] && printf -- "-f %s" "${VALUES}/${OVERLAY}/$1"
}

# Chart versions are pinned: an unpinned `helm upgrade` silently moves to whatever is newest,
# which is exactly the drift this deliverable is about eliminating everywhere else.
KPS_VERSION="65.1.0"
LOKI_VERSION="6.6.4"
ALLOY_VERSION="0.5.0"
TEMPO_VERSION="1.10.1"
OTELCOL_VERSION="0.97.0"

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo add grafana              https://grafana.github.io/helm-charts               >/dev/null
helm repo add open-telemetry       https://open-telemetry.github.io/opentelemetry-helm-charts >/dev/null
helm repo update >/dev/null

echo "[1/5] kube-prometheus-stack ${KPS_VERSION}"
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --version "${KPS_VERSION}" --namespace "${NS}" --create-namespace \
  -f "${VALUES}/kube-prometheus-stack.values.yaml" $(overlay_arg "kube-prometheus-stack.values.yaml") --wait --timeout 10m

echo "[2/5] loki ${LOKI_VERSION}"
helm upgrade --install loki grafana/loki \
  --version "${LOKI_VERSION}" --namespace "${NS}" \
  -f "${VALUES}/loki.values.yaml" $(overlay_arg "loki.values.yaml") --wait --timeout 10m

echo "[3/5] tempo ${TEMPO_VERSION}"
helm upgrade --install tempo grafana/tempo \
  --version "${TEMPO_VERSION}" --namespace "${NS}" \
  -f "${VALUES}/tempo.values.yaml" $(overlay_arg "tempo.values.yaml") --wait --timeout 10m

# Alloy after Loki, and the Collector after Tempo: both are write-side clients of the store
# above them, and starting them first just means a pod logging connection refused until it lands.
echo "[4/5] alloy ${ALLOY_VERSION}"
helm upgrade --install alloy grafana/alloy \
  --version "${ALLOY_VERSION}" --namespace "${NS}" \
  -f "${VALUES}/alloy.values.yaml" $(overlay_arg "alloy.values.yaml") --wait --timeout 10m

echo "[5/5] opentelemetry-collector ${OTELCOL_VERSION}"
helm upgrade --install otel-collector open-telemetry/opentelemetry-collector \
  --version "${OTELCOL_VERSION}" --namespace "${NS}" \
  -f "${VALUES}/otel-collector.values.yaml" $(overlay_arg "otel-collector.values.yaml") --wait --timeout 10m

kubectl get pods -n "${NS}"
echo "OK: PLG-T stack ready in namespace ${NS}."
