#!/usr/bin/env bash
# scripts/observability-smoke.sh - the round-trip check: one request, three pillars.
#
# Sends a single request carrying an id it invented, then proves that the SAME request is
# findable in all three stores:
#
#   metric   -> Prometheus has http_server_requests_seconds_count for the endpoint
#   business -> Prometheus has taxcalc_liability_recomputed_total, the application's own counter
#   log      -> Loki returns the line carrying that correlation id
#   trace    -> Tempo returns the trace whose id the request was sent with
#
# Reaching the stores over `kubectl port-forward` rather than from a pod inside the cluster is
# deliberate: it needs no extra container image (this cluster cannot pull one - see the README's
# W5 D5 section), and it works identically on a laptop and on a CI runner.
set -euo pipefail

NS="${NS:-taxcalc-dev}"
OBS_NS="${OBS_NS:-monitoring}"
APP="${APP:-taxcalc-api}"
URI_PATTERN="${URI_PATTERN:-/api/v1/taxpayers/{id}}"
TAXPAYER_ID="${TAXPAYER_ID:-txp_synth_001}"
REQUESTS="${REQUESTS:-20}"
# Loki/Tempo ingest and Prometheus scrape are all asynchronous; this is the ceiling, not a sleep.
DEADLINE_SECONDS="${DEADLINE_SECONDS:-120}"

APP_PORT=18080; PROM_PORT=19090; LOKI_PORT=13100; TEMPO_PORT=13200
PIDS=()
cleanup() { for pid in "${PIDS[@]:-}"; do kill "${pid}" 2>/dev/null || true; done; }
trap cleanup EXIT

forward() {  # forward <namespace> <target> <local:remote>
  kubectl port-forward -n "$1" "$2" "$3" >/dev/null 2>&1 &
  PIDS+=("$!")
}

wait_for_port() {  # wait_for_port <port> - port-forward is ready when the socket accepts
  for _ in $(seq 1 40); do
    if curl -s -o /dev/null "http://127.0.0.1:$1" 2>/dev/null || [ $? -ne 7 ]; then return 0; fi
    sleep 0.5
  done
  echo "ERROR: port-forward on $1 never became reachable" >&2
  return 1
}

echo "==> port-forwards"
forward "${NS}"     "svc/${APP}"                                "${APP_PORT}:8080"
forward "${OBS_NS}" "svc/kube-prometheus-stack-prometheus"      "${PROM_PORT}:9090"
forward "${OBS_NS}" "svc/loki"                                  "${LOKI_PORT}:3100"
forward "${OBS_NS}" "svc/tempo"                                 "${TEMPO_PORT}:3100"
for port in "${APP_PORT}" "${PROM_PORT}" "${LOKI_PORT}" "${TEMPO_PORT}"; do wait_for_port "${port}"; done

# One id per run, so a re-run never matches the previous run's data and reports a false pass.
CORRELATION_ID="smoke-$(date +%s)-$$"
# A W3C traceparent with the sampled flag (the trailing 01) set. The Deployment samples at 10%
# with parentbased_traceidratio, and "parentbased" means an inbound sampled decision is honoured
# - so this request is traced with certainty instead of nine times out of ten not being.
TRACE_ID="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
SPAN_ID="$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
TRACEPARENT="00-${TRACE_ID}-${SPAN_ID}-01"

echo "==> [1/5] ${REQUESTS} requests to /api/v1/taxpayers/${TAXPAYER_ID} (correlationId=${CORRELATION_ID})"
# The endpoint is JWT-gated (W3 D1) and this script deliberately presents no token: a 401 still
# traverses CorrelationIdFilter, still produces an http_server_requests sample, a JSON log line
# and a server span, which is exactly what is under test here. Authentication is TaxpayerSecurityIT's
# job, not the observability pipeline's.
for i in $(seq 1 "${REQUESTS}"); do
  curl -s -o /dev/null \
    -H "X-Correlation-Id: ${CORRELATION_ID}" \
    -H "traceparent: ${TRACEPARENT}" \
    "http://127.0.0.1:${APP_PORT}/api/v1/taxpayers/${TAXPAYER_ID}"
done

# The same lookup again through GraphQL, which is not JWT-gated (W3 D4), so the read actually
# reaches TaxpayerLookupService and increments the business counter. Without this the REST 401s
# alone would leave taxcalc_liability_recomputed_total with no series at all, and the assertion
# below would be checking a metric nothing had ever emitted.
curl -s -o /dev/null -X POST \
  -H "Content-Type: application/json" \
  -H "X-Correlation-Id: ${CORRELATION_ID}" \
  -d "{\"query\":\"{ taxpayer(id: \\\"${TAXPAYER_ID}\\\") { id } }\"}" \
  "http://127.0.0.1:${APP_PORT}/graphql"

# Retry loop rather than a fixed sleep: each store is asynchronous with a different lag, and a
# sleep long enough for the slowest one wastes the same time on every green run.
await() {  # await <label> <command...>
  local label="$1"; shift
  local deadline=$(( SECONDS + DEADLINE_SECONDS ))
  until "$@"; do
    if (( SECONDS >= deadline )); then
      echo "ERROR: ${label} not satisfied within ${DEADLINE_SECONDS}s" >&2
      return 1
    fi
    sleep 3
  done
  echo "    OK: ${label}"
}

prom_has_business_counter() {
  # The one custom metric outside the http_server_requests family. Checked separately from the
  # RED series because it proves a different thing: that the application's own instrumentation
  # ran, not merely that Spring Boot's built-in server timing did.
  curl -s -G "http://127.0.0.1:${PROM_PORT}/api/v1/query" \
    --data-urlencode "query=sum(taxcalc_liability_recomputed_total{app=\"${APP}\"})" \
    | grep -q '"value"'
}

prom_has_series() {
  curl -s -G "http://127.0.0.1:${PROM_PORT}/api/v1/query" \
    --data-urlencode "query=sum(http_server_requests_seconds_count{app=\"${APP}\",uri=\"${URI_PATTERN}\"})" \
    | grep -q '"value"'
}

loki_has_line() {
  # Note the shape of this query: the stream is selected by LABEL (app), then the correlation id
  # is a line filter over the body. That is the whole label-discipline argument in one line - see
  # manifests/observability/LABELS.md.
  local start_ns end_ns
  start_ns=$(( ($(date +%s) - 900) * 1000000000 ))
  end_ns=$(( $(date +%s) * 1000000000 ))
  curl -s -G "http://127.0.0.1:${LOKI_PORT}/loki/api/v1/query_range" \
    --data-urlencode "query={app=\"${APP}\"} |= \`${CORRELATION_ID}\`" \
    --data-urlencode "start=${start_ns}" --data-urlencode "end=${end_ns}" --data-urlencode "limit=5" \
    | grep -q "${CORRELATION_ID}"
}

tempo_has_trace() {
  # Fetched by trace id, not searched by service: the id is known exactly, so this asserts that
  # THIS request's trace landed, not merely that some trace from this service did.
  curl -s -o /dev/null -w '%{http_code}' \
    "http://127.0.0.1:${TEMPO_PORT}/api/traces/${TRACE_ID}" | grep -q '^200$'
}

echo "==> [2/5] Prometheus has http_server_requests_seconds_count for ${URI_PATTERN}"
await "metric" prom_has_series

echo "==> [3/5] Prometheus has the custom business counter taxcalc_liability_recomputed_total"
await "business metric" prom_has_business_counter

echo "==> [4/5] Loki has the JSON log line carrying correlationId=${CORRELATION_ID}"
await "log" loki_has_line

echo "==> [5/5] Tempo has trace ${TRACE_ID}"
await "trace" tempo_has_trace

echo "OK: metrics + logs + traces all visible for one request (correlationId=${CORRELATION_ID}, traceId=${TRACE_ID})."
