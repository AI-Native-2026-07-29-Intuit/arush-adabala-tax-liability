#!/usr/bin/env bash
# scripts/smoke.sh - boot the stack, assert every layer responds.
# Identical script runs on engineer laptops and in CI.
#
# Exits 0 on green smoke; non-zero with logs printed on failure.

set -euo pipefail

# Local/standalone runs get a unique project per invocation so parallel runs
# don't collide. CI pins COMPOSE_PROJECT_NAME itself (see compose-ci.yml) so
# this script's own `up` reuses the exact stack a prior "Compose up" step
# already brought healthy, instead of standing up a second one that would
# collide with it on the same host ports.
PROJECT="${COMPOSE_PROJECT_NAME:-taxcalc_dev_smoke_$$}"
COMPOSE="docker compose -p ${PROJECT}"
HOST_PORT="${HOST_PORT:-8080}"

cleanup() {
    rc=$?
    echo ""
    echo "--- Final state ---"
    ${COMPOSE} ps || true
    if [ "${rc}" != "0" ]; then
        echo ""
        echo "--- Last 200 log lines per service ---"
        # This script's own trap tears its project down before returning
        # control, so it's also the only place that can still capture logs
        # first - written to compose-logs.txt (repo root) for CI to pick up
        # and upload as an artefact after this script has already exited.
        ${COMPOSE} logs --no-color --tail=200 | tee compose-logs.txt || true
    fi
    ${COMPOSE} down --volumes --remove-orphans || true
    exit ${rc}
}
trap cleanup EXIT

echo "Bringing up stack as project ${PROJECT}..."
${COMPOSE} up -d --wait --wait-timeout 90

echo "Confirming all services are healthy..."
# `docker compose ps --format json` emits one JSON object per line (JSON
# Lines), not a JSON array - no `.[]` needed/wanted here.
unhealthy=$(${COMPOSE} ps --format json \
  | jq -r 'select(.Health != "healthy" and .Health != "") | .Name')
if [ -n "${unhealthy}" ]; then
  echo "ERROR: services not healthy: ${unhealthy}"
  exit 1
fi

echo "Smoke 1/3: GET /actuator/health/readiness"
curl --silent --show-error --fail \
    --retry 15 --retry-delay 2 --retry-connrefused \
    "http://localhost:${HOST_PORT}/actuator/health/readiness" \
    | jq -e '.status == "UP"'

echo "Smoke 2/3: GET /api/v1/taxpayers/txp_synth_001 (200, 401, or 404 all acceptable)"
# The route requires a Bearer JWT (@PreAuthorize) - this script makes no
# attempt to obtain one, so an unauthenticated call correctly gets 401. What
# this check actually proves is that the API layer (security filter chain,
# routing, JSON error handling) is alive and answering with an expected,
# documented status - not that the route is open or that the id exists.
status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
              "http://localhost:${HOST_PORT}/api/v1/taxpayers/txp_synth_001")
if [ "${status}" != "200" ] && [ "${status}" != "401" ] && [ "${status}" != "404" ]; then
  echo "ERROR: unexpected status ${status} from /api/v1/taxpayers/txp_synth_001"
  exit 1
fi

echo "Smoke 3/3: GET /actuator/health/liveness == UP"
curl --silent --show-error --fail \
    "http://localhost:${HOST_PORT}/actuator/health/liveness" \
    | jq -e '.status == "UP"'

echo ""
echo "Smoke OK. Stack ready in <90s, all three checks green."
