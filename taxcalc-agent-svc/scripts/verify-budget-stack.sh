#!/usr/bin/env bash
# taxcalc-agent-svc/scripts/verify-budget-stack.sh
#
# Verify cfn/agent-svc-budget.yaml as far as it can honestly be verified without an AWS account
# and a month of real spend - and say plainly which parts those are.
#
# THE POINT OF THIS SCRIPT IS THE NEGATIVE CONTROL.
#
# The obvious way to "verify" a CloudFormation template locally is to deploy it against the floci
# emulator this repo already uses (W5 D4, W6 D1). That is worthless here, and worse than
# worthless because it looks convincing. Measured against floci 2.0.1:
#
#   * `aws budgets describe-budgets`              -> UnknownOperationException
#   * `aws budgets describe-budget-actions-...`   -> UnknownOperationException
#   * `aws cloudformation deploy` of this template -> CREATE_COMPLETE
#   * `aws cloudformation deploy` of a template with FOUR WRONG PROPERTY NAMES that real
#     CloudFormation rejects outright                -> also CREATE_COMPLETE
#   * `aws cloudformation validate-template` on that same broken template -> accepted, silently
#
# floci implements no Budgets service at all, so its CloudFormation treats AWS::Budgets::* as an
# opaque passthrough: it stores the properties and reports success for anything. A green floci
# deploy of this stack proves the template is well-formed YAML whose parameters, !Refs, Outputs
# and DependsOn resolve. It proves NOTHING about whether the resources are valid. This is the
# repo's recurring lesson in its third instance - "floci's most confident answer was its
# wrongest" (README, W6 D3).
#
# So the authority here is AWS's OWN published resource provider schemas - the same artefacts
# CloudFormation validates against server-side - which cfn-lint bundles and checks. That schema
# says, for AWS::Budgets::BudgetsAction:
#
#     ActionThreshold : required ['Value','Type'],   additionalProperties: false
#     Subscriber      : required ['Type','Address'], additionalProperties: false
#
# while the sibling AWS::Budgets::Budget spells its subscriber field SubscriptionType. Two
# resources in one service, the same concepts spelled differently - which is exactly the mistake
# this template originally made in four places.
#
# Usage:
#   ./scripts/verify-budget-stack.sh            # schema gate + negative control (no Docker)
#   FLOCI=1 ./scripts/verify-budget-stack.sh    # also exercise the stack lifecycle on floci
set -euo pipefail

cd "$(dirname "$0")/.."
TEMPLATE="cfn/agent-svc-budget.yaml"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

CFN_LINT="${CFN_LINT:-cfn-lint}"
command -v "$CFN_LINT" >/dev/null 2>&1 || CFN_LINT="uv tool run cfn-lint"

echo "== 1. the committed template validates against AWS's published schema =="
$CFN_LINT "$TEMPLATE"
echo "   OK: $TEMPLATE is clean"

echo
echo "== 2. NEGATIVE CONTROL: the same gate must REJECT the known-bad property names =="
# Reintroduce exactly the four names that were wrong before cfn-lint caught them. A gate that
# has never been seen to fail is a gate nobody knows works - the same reasoning as the wheel
# probe in taxcalc_mcp_server-ci.yml.
sed -e 's/^        Value: 100$/        ActionThresholdValue: 100/' \
    -e 's/^        Type: PERCENTAGE$/        ActionThresholdType: PERCENTAGE/' \
    -e 's/^        - Type: EMAIL$/        - SubscriptionType: EMAIL/' \
    "$TEMPLATE" > "$WORK/broken.yaml"

if diff -q "$TEMPLATE" "$WORK/broken.yaml" >/dev/null; then
  echo "   FAIL: the negative control is identical to the template - the sed patterns have"
  echo "         drifted, so this control proves nothing. Fix the patterns."
  exit 1
fi

if $CFN_LINT "$WORK/broken.yaml" >/dev/null 2>&1; then
  echo "   FAIL: cfn-lint ACCEPTED a template real CloudFormation rejects."
  echo "         The schema gate is not gating - do not trust step 1."
  exit 1
fi
echo "   OK: cfn-lint rejects the broken variant, so step 1 is a real gate"

echo
echo "== 3. the cap is configured to fire, and denies exactly what it should =="
# Reproduces IAM's decision procedure offline - an explicit Deny beats any Allow - because no
# emulator evaluates policy. floci returns UnsupportedOperation for simulate-custom-policy, and
# the W6 D1 experiment recorded in scripts/oidc-trust-simulate.py showed it issuing credentials
# for a FORGED token, i.e. never reading the trust policy at all.
if command -v uv >/dev/null 2>&1; then
  uv run --project . python scripts/simulate_budget_deny.py
else
  python3 scripts/simulate_budget_deny.py
fi

echo
echo "== 4. what is NOT verified here, and cannot be =="
cat <<'EOF'
   - That AWS accepts the stack. Only a real account can answer that; the schema check above is
     the closest offline equivalent.
   - That the Budgets SERVICE fires the action at 100%. Step 3 checks the configuration that
     decides whether it would - threshold, approval model, notification type, action type - but
     AWS's own behaviour is AWS's to guarantee.
   - That the account's SCPs or permission boundaries do not alter the decision.
   - That BudgetsActionRoleArn / ServiceRoleName exist in the target account. They are template
     Parameters, so this file cannot know.
   AND THE ONE THAT MATTERS MOST: the agent calls api.anthropic.com DIRECTLY, so its model spend
   never crosses an AWS-controlled surface. The execute-api deny is inert until that traffic is
   routed through the W3 D1 proxy; today the enforceable statement is the Secrets Manager one,
   which stops the key being re-read rather than stopping a call in flight. See the template
   header and RUNBOOK.md "BudgetAction fired".
EOF

if [ "${FLOCI:-0}" = "1" ]; then
  echo
  echo "== 5. floci: stack lifecycle only (it validates NO Budgets property - see the header) =="
  export AWS_ENDPOINT_URL="${AWS_ENDPOINT_URL:-http://localhost:4566}"
  export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
  export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
  export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
  STACK="taxcalc-agent-budget-verify"

  aws cloudformation delete-stack --stack-name "$STACK" >/dev/null 2>&1 || true
  aws cloudformation deploy --stack-name "$STACK" --template-file "$TEMPLATE" \
      --parameter-overrides MonthlyBudgetUsd=4000 >/dev/null
  STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK" \
      --query 'Stacks[0].StackStatus' --output text)
  echo "   stack status: $STATUS"
  [ "$STATUS" = "CREATE_COMPLETE" ] || { echo "   FAIL: stack did not reach CREATE_COMPLETE"; exit 1; }

  # The Outputs are the one thing floci genuinely exercises: they prove !Ref resolution and
  # parameter plumbing, which a YAML parse alone does not.
  echo "   outputs (proves !Ref + parameter resolution):"
  aws cloudformation describe-stacks --stack-name "$STACK" \
      --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output text | sed 's/^/     /'

  echo "   reminder: floci reports CREATE_COMPLETE for the BROKEN template too."
  aws cloudformation delete-stack --stack-name "$STACK" >/dev/null 2>&1 || true
fi

echo
echo "VERIFIED: template valid against AWS's published schema; gate proven by negative control."
