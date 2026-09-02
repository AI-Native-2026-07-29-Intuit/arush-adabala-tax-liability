#!/usr/bin/env bash
# scripts/floci-parity.sh - backfill the two AWS behaviours the floci emulator does not implement,
# so the W5 D4 verification commands can be run end to end without an AWS account.
#
# ============================ READ THIS BEFORE QUOTING THE OUTPUT ============================
# This script makes a LOCAL EMULATOR behave like AWS. It does not test AWS, and it does not make
# the emulator's answers equivalent to AWS's. Two gaps are filled:
#
#   1. IAM  - floci's CloudFormation does not expand SAM policy connectors, so the execution role
#             is created without the DynamoDBReadPolicy statement. The statement applied here is
#             NOT hand-written: it is extracted from AWS's own SAM transform (scripts/sam-transform.py)
#             run against this repo's template.yaml. So the policy CONTENT is authoritative - it is
#             what CloudFormation would attach - while the ACT of attaching it is ours, not the
#             deploy's.
#
#   2. EMF  - floci stores embedded-metric-format log lines as ordinary log events and never
#             extracts metrics from them. This script parses the function's real emitted EMF lines
#             per the EMF spec and publishes what CloudWatch's extractor would have published.
#             That proves OUR payload is well-formed and carries the right namespace, metric and
#             dimensions. It does NOT prove CloudWatch would accept it - the parser here is ours.
#
# If you paste this output anywhere, paste this provenance with it. Presented bare, it is
# indistinguishable from a real AWS deploy, which would be misleading.
# =============================================================================================
set -euo pipefail

STACK="${STACK:-taxcalc-lambda-${STAGE:-dev}}"
STAGE="${STAGE:-dev}"
REGION="${AWS_REGION:-us-east-1}"
FUNCTION="taxcalc-taxpayer-lookup-${STAGE}"
: "${AWS_ENDPOINT_URL:?set AWS_ENDPOINT_URL to the emulator, e.g. http://localhost:4566}"

echo "=============================================================================="
echo " floci parity backfill - EMULATOR ONLY. See the header of this script."
echo " stack=${STACK} function=${FUNCTION} endpoint=${AWS_ENDPOINT_URL}"
echo "=============================================================================="

# ----- 1. IAM: attach the policy AWS's transform says belongs on the role ---------------------
echo ""
echo "==> [1/2] IAM: extracting DynamoDBReadPolicy from the SAM transform"
TRANSFORM_PY="${TRANSFORM_PY:-python3}"
# shellcheck disable=SC2016  # the inline Python uses ${...} CloudFormation placeholders verbatim.
POLICY_JSON=$("${TRANSFORM_PY}" scripts/sam-transform.py template.yaml \
  | python3 -c '
import json, sys
t = json.load(sys.stdin)["Resources"]
pol = t["TaxpayerLookupFunctionRole"]["Properties"]["Policies"][0]
doc = pol["PolicyDocument"]
# Resolve the Fn::Sub ARNs the transform leaves symbolic - CloudFormation would do this at
# deploy time. Account 000000000000 is what the emulator reports for every caller.
def resolve(node, table):
    if isinstance(node, dict) and "Fn::Sub" in node:
        tpl, _ = node["Fn::Sub"]
        return (tpl.replace("${AWS::Partition}", "aws")
                   .replace("${AWS::Region}", "us-east-1")
                   .replace("${AWS::AccountId}", "000000000000")
                   .replace("${tableName}", table))
    return node
table = "taxpayers-" + __import__("os").environ.get("STAGE", "dev")
for st in doc["Statement"]:
    st["Resource"] = [resolve(r, table) for r in st["Resource"]]
print(json.dumps({"name": pol["PolicyName"], "doc": doc}))')

POLICY_NAME=$(echo "${POLICY_JSON}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
POLICY_DOC=$(echo "${POLICY_JSON}" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["doc"]))')

# shellcheck disable=SC2016  # JMESPath, not shell expansion.
ROLE=$(aws lambda get-function-configuration --function-name "${FUNCTION}" \
  --region "${REGION}" --query 'Role' --output text | awk -F/ '{print $NF}')
echo "    role: ${ROLE}"
aws iam put-role-policy --role-name "${ROLE}" \
  --policy-name "${POLICY_NAME}" --policy-document "${POLICY_DOC}" --region "${REGION}"

echo ""
echo "--- aws iam get-role-policy (EMULATOR; policy content from AWS's SAM transform) ---"
aws iam get-role-policy --role-name "${ROLE}" --policy-name "${POLICY_NAME}" \
  --region "${REGION}" --output json

# ----- 2. EMF: publish what CloudWatch's extractor would have published ------------------------
echo ""
echo "==> [2/2] EMF: parsing the function's emitted metric lines"
aws logs filter-log-events --log-group-name "/aws/lambda/${FUNCTION}" \
  --region "${REGION}" --query 'events[].message' --output json 2>/dev/null \
  | python3 -c '
import json, subprocess, sys, os
region = os.environ.get("AWS_REGION", "us-east-1")
published = 0
for msg in json.load(sys.stdin) or []:
    try:
        doc = json.loads(msg)
    except (ValueError, TypeError):
        continue                     # ordinary log line, not EMF
    if "_aws" not in doc:
        continue
    for directive in doc["_aws"]["CloudWatchMetrics"]:
        ns = directive["Namespace"]
        for dim_set in directive["Dimensions"] or [[]]:
            dims = ",".join(f"{k}={doc[k]}" for k in dim_set if k in doc)
            for metric in directive["Metrics"]:
                name = metric["Name"]
                if name not in doc:
                    continue         # spec violation: metric name must be a root member
                cmd = ["aws", "cloudwatch", "put-metric-data", "--namespace", ns,
                       "--region", region, "--metric-name", name,
                       "--value", str(doc[name]), "--unit", metric.get("Unit", "None")]
                if dims:
                    cmd += ["--dimensions", dims]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
                published += 1
print(f"    parsed and published {published} datapoint(s) from real EMF lines")'

echo ""
echo "--- aws cloudwatch list-metrics --namespace TaxcalcDev (EMULATOR; extractor is ours) ---"
aws cloudwatch list-metrics --namespace TaxcalcDev --region "${REGION}" --output json

echo ""
echo "=============================================================================="
echo " Done. Both outputs above came from a local emulator that was helped along."
echo " Quote them with the provenance in this script's header, never bare."
echo "=============================================================================="
