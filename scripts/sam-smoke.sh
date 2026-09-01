#!/usr/bin/env bash
# scripts/sam-smoke.sh - assert the deployed Lambda + HTTP API actually works.
# Three checks: a known-good path, a known-bad path, and proof that logs are flowing.
# Runs identically on a laptop and in CI.
set -euo pipefail

STACK="${STACK:-taxcalc-lambda-${STAGE:-dev}}"
STAGE="${STAGE:-dev}"
REGION="${AWS_REGION:-us-east-1}"
BODY_FILE="$(mktemp -t sam-smoke-body)"
trap 'rm -f "${BODY_FILE}"' EXIT

# 1. Resolve the HttpApiUrl from the stack's own outputs rather than hardcoding it: the API id
#    is regenerated whenever the stack is deleted and recreated, which this deliverable does
#    deliberately and repeatedly.
# shellcheck disable=SC2016  # JMESPath backticks are literal, not shell expansion.
URL=$(aws cloudformation describe-stacks \
  --stack-name "${STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`HttpApiUrl`].OutputValue' \
  --output text)

if [ -z "${URL}" ] || [ "${URL}" = "None" ]; then
  echo "ERROR: HttpApiUrl output not found on stack ${STACK}."
  exit 1
fi
echo "HttpApiUrl: ${URL}"

CORR="ci-smoke-$(date +%s)"

# 2. Smoke 1/3: a known-good path parameter reaches the handler.
#    200 and 404 are both accepted: this stack creates an empty table, so until something seeds
#    txp_synth_001 a 404 is the *correct* answer and still proves the whole chain (API Gateway
#    route -> alias -> function -> DynamoDB GetItem) works end to end. A 5xx, a 403, or a
#    connection failure would not.
echo "Smoke 1/3: GET /taxpayers/txp_synth_001 (expect 200, or 404 on an unseeded table)"
status=$(curl --silent --show-error --output "${BODY_FILE}" --write-out '%{http_code}' \
  --max-time 30 \
  -H "x-correlation-id: ${CORR}" \
  -D "${BODY_FILE}.hdr" \
  "${URL}/taxpayers/txp_synth_001")
if [ "${status}" != "200" ] && [ "${status}" != "404" ]; then
  echo "ERROR: unexpected status ${status} on known-good path."
  cat "${BODY_FILE}"
  exit 1
fi
cat "${BODY_FILE}"; echo ""

# 2b. The correlation id the caller supplied must come back on the response, on either status.
#     This is the end-to-end half of the unit test's assertion - it proves the header survives
#     API Gateway's v2 payload mapping, not just the handler's own logic.
if ! grep -qi "^x-correlation-id: ${CORR}" "${BODY_FILE}.hdr"; then
  echo "ERROR: response did not echo x-correlation-id: ${CORR}"
  cat "${BODY_FILE}.hdr"
  exit 1
fi
echo "Correlation id echoed back: ${CORR}"
rm -f "${BODY_FILE}.hdr"

# 3. Smoke 2/3: a path that matches no route falls back to API Gateway's own 404. This catches
#    a route table that quietly stopped matching (e.g. a renamed path parameter), which the
#    check above cannot distinguish from a healthy miss.
echo "Smoke 2/3: GET /taxpayers/ (expect 404 from the API Gateway route table)"
status=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 30 \
  "${URL}/taxpayers/")
if [ "${status}" != "404" ]; then
  echo "ERROR: expected 404 from API Gateway route miss, got ${status}."
  exit 1
fi

# 4. Smoke 3/3: a REPORT line for a recent invocation is present in CloudWatch Logs. A 200 with
#    no logs behind it means the explicit LogGroup and the function have drifted apart - which
#    is invisible from the HTTP response alone.
echo "Smoke 3/3: at least one REPORT line in CloudWatch for the function"
# shellcheck disable=SC2016  # JMESPath backticks are literal, not shell expansion.
FN_NAME=$(aws cloudformation describe-stacks \
  --stack-name "${STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`FunctionName`].OutputValue' \
  --output text)
sleep 5  # logs need a moment to flush from the runtime to CloudWatch.
report_count=$(aws logs filter-log-events \
  --region "${REGION}" \
  --log-group-name "/aws/lambda/${FN_NAME}" \
  --start-time $(( $(date +%s) * 1000 - 120000 )) \
  --filter-pattern '"REPORT"' \
  --query 'length(events)' --output text 2>/dev/null || echo 0)
if [ "${report_count}" -lt 1 ]; then
  echo "ERROR: no REPORT lines in CloudWatch in the last 120s."
  exit 1
fi

echo ""
echo "Smoke OK. Function reachable; correlation id propagates; logs flowing; route table sane."
