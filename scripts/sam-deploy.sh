#!/usr/bin/env bash
# scripts/sam-deploy.sh - one-shot validate + build + deploy for the W5 D4 Lambda stack.
# Idempotent: re-running just re-applies the change set (an empty change set is not an error).
# Runs identically on a laptop and in CI.
set -euo pipefail

STACK="${STACK:-taxcalc-lambda-${STAGE:-dev}}"
STAGE="${STAGE:-dev}"
REGION="${AWS_REGION:-us-east-1}"

echo "==> stack=${STACK} stage=${STAGE} region=${REGION}"

# 1. Validate the template (catches malformed SAM locally, before AWS is ever called).
#    --lint runs cfn-lint over the transformed template, which catches typed-property errors
#    (a bad ComparisonOperator, an unknown Runtime) that plain validation lets through.
echo "==> sam validate --lint"
sam validate --lint --region "${REGION}"

# 2. Build under a Lambda-parity container so the artifact is byte-comparable with what the
#    runtime will actually execute, rather than with whatever JDK happens to be on this host.
echo "==> sam build --use-container"
sam build --use-container

# 3. Deploy. The first run is interactive (--guided) and writes samconfig.toml, which is
#    gitignored; every subsequent run is non-interactive and reuses it.
#    --capabilities is required either way: the transform generates the function's IAM role,
#    and CloudFormation refuses to create IAM without an explicit acknowledgement.
if [ ! -f samconfig.toml ]; then
  echo "==> sam deploy --guided (first-time setup)"
  sam deploy --guided \
    --stack-name "${STACK}" \
    --region "${REGION}" \
    --parameter-overrides "StageName=${STAGE}" \
    --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND
else
  echo "==> sam deploy"
  sam deploy \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset \
    --stack-name "${STACK}" \
    --region "${REGION}" \
    --parameter-overrides "StageName=${STAGE}" \
    --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
    --resolve-s3
fi

# 4. Print the stack outputs; sam-smoke.sh resolves HttpApiUrl from the same place.
echo ""
echo "==> Outputs:"
aws cloudformation describe-stacks \
  --stack-name "${STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs' \
  --output table

echo ""
echo "Deploy OK. Next: ./scripts/sam-smoke.sh"
echo "Tear down with: sam delete --stack-name ${STACK} --region ${REGION}"
