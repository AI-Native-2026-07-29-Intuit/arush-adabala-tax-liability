#!/usr/bin/env bash
# scripts/oidc-bootstrap.sh - create the GitHub OIDC provider and the deploy role the
# serverless CI workflow assumes (W5 D4 Task 4).
#
# Run it against a real AWS dev account to do the actual setup:
#   AWS_PROFILE=dev ./scripts/oidc-bootstrap.sh
#
# Or against the floci emulator to author and inspect the trust policy without an account:
#   AWS_ENDPOINT_URL=http://localhost:4566 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \
#     ./scripts/oidc-bootstrap.sh
#
# Against the emulator this proves the trust policy is well-formed and pinned to the right
# subject. It does NOT prove GitHub can assume the role - only a real account can show that.
# The GitHub half is verified separately, by the workflow's own "Inspect OIDC token claims"
# step, which mints a real token and prints the `sub` this policy must match.
set -euo pipefail

REPO="${REPO:-AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability}"
BRANCH="${BRANCH:-main}"

# The `sub` claim the trust policy pins to. DO NOT assume the textbook
# "repo:<org>/<repo>:ref:refs/heads/<branch>" form - this organisation customises the OIDC
# subject to embed numeric ids, observed live from a token minted by the workflow's own
# "Inspect OIDC token claims" step:
#
#   repo:AI-Native-2026-07-29-Intuit@309728071/arush-adabala-tax-liability@1317703842:pull_request
#
# A policy pinned to the plain form would never match, and the failure is opaque:
# "Not authorized to perform sts:AssumeRoleWithWebIdentity". So take the value from a real
# token rather than constructing it. The default below is the textbook form and is almost
# certainly wrong HERE; override it:
#
#   SUBJECT='repo:ORG@123/REPO@456:ref:refs/heads/main' ./scripts/oidc-bootstrap.sh
#
# Note the deploy job only runs on push to main, so the subject to pin ends in
# ":ref:refs/heads/main" - NOT the ":pull_request" seen on PR runs. Confirm that by reading
# the claims off a push-to-main run before locking the policy down.
SUBJECT="${SUBJECT:-repo:${REPO}:ref:refs/heads/${BRANCH}}"
ROLE_NAME="${ROLE_NAME:-taxcalc-serverless-deploy}"
REGION="${AWS_REGION:-us-east-1}"

ACCOUNT=$(aws sts get-caller-identity --query 'Account' --output text)
PROVIDER_ARN="arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"

echo "==> account=${ACCOUNT} role=${ROLE_NAME}"
echo "==> pinning sub: ${SUBJECT}"

# 1. The OIDC provider. One per account; creating it twice is an error, so tolerate that.
echo "==> ensuring the GitHub OIDC provider exists"
aws iam create-open-id-connect-provider \
  --url "https://token.actions.githubusercontent.com" \
  --client-id-list "sts.amazonaws.com" \
  --thumbprint-list "6938fd4d98bab03faadb97b34396831e3780aea1" \
  >/dev/null 2>&1 || echo "    (already present)"

# 2. The trust policy. The two conditions are the whole security boundary:
#      aud = sts.amazonaws.com  - the token was minted FOR AWS, not for some other consumer
#      sub = repo:<org>/<repo>:ref:refs/heads/<branch>
#            - pins it to this repository AND this branch. Without the `sub` condition ANY
#              GitHub repository in the world could assume this role; a wildcard on the branch
#              would let any branch in this repo deploy, including one opened by a fork PR.
TRUST=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Federated": "${PROVIDER_ARN}" },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "${SUBJECT}"
        }
      }
    }
  ]
}
JSON
)

echo "==> creating/updating role ${ROLE_NAME}"
if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "${ROLE_NAME}" --policy-document "${TRUST}"
else
  aws iam create-role --role-name "${ROLE_NAME}" \
    --description "GitHub Actions OIDC deploy role for the taxcalc-taxpayer-lookup SAM stack" \
    --assume-role-policy-document "${TRUST}" >/dev/null
fi

# 3. Deploy permissions. Scoped to the services this stack actually creates rather than
#    PowerUserAccess - the same least-privilege argument as the function's own execution role,
#    applied to the thing that builds it. iam:* is unavoidable here because the SAM transform
#    generates the execution role, but it is scoped to role/taxcalc-lambda-*.
#
#    NOTE the deliberate looseness: the first statement uses Resource "*". A deploy role has to
#    create resources whose ARNs do not exist yet, so it cannot be ARN-scoped the way the
#    function's execution role is. The boundary that matters for this role is the TRUST policy
#    above - only this repo, on this branch, can assume it - not the permission set. If you want
#    it tighter, add aws:ResourceTag conditions and tag the stack.
DEPLOY_POLICY=$(cat <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow",
      "Action": ["cloudformation:*", "lambda:*", "apigateway:*", "dynamodb:*",
                 "logs:*", "cloudwatch:*", "s3:*", "xray:*"],
      "Resource": "*" },
    { "Effect": "Allow",
      "Action": ["iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:PassRole",
                 "iam:AttachRolePolicy", "iam:DetachRolePolicy",
                 "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:GetRolePolicy",
                 "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:TagRole"],
      "Resource": "arn:aws:iam::*:role/taxcalc-lambda-*" }
  ]
}
JSON
)
aws iam put-role-policy --role-name "${ROLE_NAME}" \
  --policy-name "taxcalc-serverless-deploy" --policy-document "${DEPLOY_POLICY}"

ROLE_ARN=$(aws iam get-role --role-name "${ROLE_NAME}" --query 'Role.Arn' --output text)

echo ""
echo "==> trust policy as stored:"
aws iam get-role --role-name "${ROLE_NAME}" --query 'Role.AssumeRolePolicyDocument' --output json

echo ""
echo "=============================================================================="
echo " Role ARN: ${ROLE_ARN}"
echo ""
echo " Set it as a GitHub Actions repository VARIABLE (not a secret - an ARN is not"
echo " confidential, and hiding it only makes failures harder to read):"
echo ""
echo "   gh variable set AWS_DEPLOY_ROLE_ARN --body '${ROLE_ARN}'"
echo "   gh variable set AWS_REGION --body '${REGION}'"
echo "=============================================================================="
