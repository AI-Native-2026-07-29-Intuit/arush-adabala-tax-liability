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
ROLE_NAME="${ROLE_NAME:-taxcalc-serverless-deploy}"
REGION="${AWS_REGION:-us-east-1}"

ACCOUNT=$(aws sts get-caller-identity --query 'Account' --output text)
PROVIDER_ARN="arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"

echo "==> account=${ACCOUNT} repo=${REPO} branch=${BRANCH} role=${ROLE_NAME}"

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
          "token.actions.githubusercontent.com:sub": "repo:${REPO}:ref:refs/heads/${BRANCH}"
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
#    generates the execution role, but it is scoped to this account's roles.
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
