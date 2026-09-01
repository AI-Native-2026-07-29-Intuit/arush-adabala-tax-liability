#!/usr/bin/env bash
# scripts/k8s-smoke.sh - assert the deployed service serves traffic.
# Runs identically on a laptop and in CI.
set -euo pipefail

NAMESPACE="taxcalc-dev"
HOST="taxcalc.dev.uptimecrew.internal"
HOST_PORT="${HOST_PORT:-8080}"

# 1. Wait for the Deployment's rollout to settle.
kubectl rollout status deploy/taxcalc-api \
  -n "${NAMESPACE}" --timeout=5m

# 2. EndpointSlice has at least one ready endpoint (catches label drift).
endpoints=$(kubectl get endpointslice \
  -n "${NAMESPACE}" \
  -l kubernetes.io/service-name=taxcalc-api \
  -o jsonpath='{.items[*].endpoints[?(@.conditions.ready==true)].addresses[0]}' \
  | tr ' ' '\n' | grep -c . || true)
if [ "${endpoints}" -lt 1 ]; then
  echo "ERROR: Service taxcalc-api has zero ready endpoints."
  echo "       Check selector/label match (Topic 4's silent failure)."
  kubectl describe svc/taxcalc-api -n "${NAMESPACE}" || true
  exit 1
fi

# 3. Smoke through the Ingress (catches end-to-end routing).
# --retry-all-errors (NOT just --retry-connrefused): the ingress-nginx
# Deployment reporting `available` doesn't guarantee its nginx workers have
# finished reloading the just-applied Ingress rule yet - that race showed up
# in CI as curl exit 52 "Empty reply from server", which --retry-connrefused
# alone does not cover (it only widens retries to connection-refused, not
# every transient failure).
RETRY_ARGS="--retry 15 --retry-delay 2 --retry-all-errors"

echo "Smoke 1/3: GET /actuator/health/readiness via Ingress"
curl --silent --show-error --fail ${RETRY_ARGS} \
  -H "Host: ${HOST}" \
  "http://localhost:${HOST_PORT}/actuator/health/readiness" \
  | grep -q '"status":"UP"'

echo "Smoke 2/3: GET /api/v1/taxpayers/txp_synth_001 (200/401/404 all acceptable - JWT-gated, see W5 D2's smoke.sh)"
status=$(curl --silent --output /dev/null --write-out '%{http_code}' ${RETRY_ARGS} \
  -H "Host: ${HOST}" \
  "http://localhost:${HOST_PORT}/api/v1/taxpayers/txp_synth_001")
if [ "${status}" != "200" ] && [ "${status}" != "401" ] && [ "${status}" != "404" ]; then
  echo "ERROR: unexpected status ${status} from /api/v1/taxpayers/txp_synth_001"
  exit 1
fi

echo "Smoke 3/3: GET /actuator/health/liveness via Ingress"
curl --silent --show-error --fail ${RETRY_ARGS} \
  -H "Host: ${HOST}" \
  "http://localhost:${HOST_PORT}/actuator/health/liveness" \
  | grep -q '"status":"UP"'

echo ""
echo "Smoke OK. Deployment serving through the Ingress."
