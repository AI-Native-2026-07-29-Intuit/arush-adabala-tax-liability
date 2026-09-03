#!/usr/bin/env bash
# scripts/observability-preload-images.sh - pull the PLG-T chart images on the HOST and import
# them into the k3d cluster.
#
# Only needed on machines whose outbound TLS is intercepted by a corporate proxy: the host's
# Docker daemon trusts the interception CA (it is in the macOS System keychain), but containerd
# inside the k3d node containers does not, so an in-cluster pull dies with
#
#   failed to resolve reference "...": tls: failed to verify certificate:
#   x509: certificate signed by unknown authority
#
# Importing sidesteps the in-cluster pull entirely - the image is already in the node's content
# store, and `imagePullPolicy: IfNotPresent` (the default for a pinned tag) never reaches out.
# On an unfiltered network this script is a no-op you can skip.
set -euo pipefail

CLUSTER="${K3D_CLUSTER:-taxcalc}"

# Pinned to the exact tags the chart versions in observability-bootstrap.sh render. Re-derive
# with: helm template rel <chart> --version <v> -f <values> | grep -Eo '^\s+image: .*'
# (prometheus-config-reloader is not in that output - the Operator injects it into the
# Prometheus/Alertmanager pods it creates from the CRs, so it has to be listed by hand.)
IMAGES=(
  quay.io/prometheus-operator/prometheus-operator:v0.77.1
  quay.io/prometheus-operator/prometheus-config-reloader:v0.77.1
  quay.io/prometheus/prometheus:v2.54.1
  quay.io/prometheus/alertmanager:v0.27.0
  quay.io/kiwigrid/k8s-sidecar:1.27.4
  # The Prometheus Operator's admission-webhook cert-generation Job. It is the only image here
  # from registry.k8s.io, and the reason this script exists at all - the in-cluster pull of it
  # was the first thing the intercepting proxy broke.
  registry.k8s.io/ingress-nginx/kube-webhook-certgen:v20221220-controller-v1.5.1-58-g787ea74b6
  ghcr.io/jimmidyson/configmap-reload:v0.12.0
  docker.io/grafana/grafana:11.2.1
  docker.io/grafana/loki:3.0.0
  docker.io/grafana/alloy:v1.2.0
  docker.io/grafana/tempo:2.5.0
  docker.io/otel/opentelemetry-collector-contrib:0.104.0
)

# The node architecture, not the host's: they match on a normal k3d install, but pulling the
# wrong one here fails in a way that does not look like an architecture problem. An amd64 image
# imported onto arm64 nodes still starts (the VM emulates x86), it just runs several times
# heavier - which surfaces as the smallest sidecars being OOMKilled (exit 137) while the big
# containers stay up, and nothing anywhere says "wrong architecture".
ARCH="$(kubectl get nodes -o jsonpath='{.items[0].status.nodeInfo.architecture}')"
echo "==> k3d nodes report architecture: ${ARCH}"

for image in "${IMAGES[@]}"; do
  echo "==> pull ${image}"
  docker pull --platform "linux/${ARCH}" --quiet "${image}"
done

echo "==> import ${#IMAGES[@]} images into k3d cluster '${CLUSTER}'"
k3d image import "${IMAGES[@]}" --cluster "${CLUSTER}" --mode direct

echo "OK: PLG-T images preloaded into ${CLUSTER}."
