#!/usr/bin/env bash
# scripts/docker-oidc-bootstrap.sh - create the two GitHub OIDC deploy roles the taxcalc-api
# CI/CD pipeline assumes (W6 D1 Task 3/4): taxcalc-api-build-push (dev, every push to main) and
# taxcalc-api-prod-deploy (prod, gated by the GitHub Environment's required reviewers).
#
# Sibling to scripts/oidc-bootstrap.sh (the W5 D4 serverless deploy role) - same structure,
# same idempotency, same SUBJECT caveat. Run it against a real AWS account:
#   AWS_PROFILE=dev ./scripts/docker-oidc-bootstrap.sh
#
# See infra/oidc/README.md before running this for real: the `SUBJECT_*` defaults below are the
# textbook "repo:<org>/<repo>:..." form, and oidc-bootstrap.sh already found - for THIS
# organization - that GitHub's real token embeds internal numeric org/repo ids instead. Confirm
# the actual claim from a live token (see that README's "Inspect OIDC token claims" pointer)
# before trusting the defaults here.
set -euo pipefail

REPO="${REPO:-AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability}"
ECR_REPO="${ECR_REPO:-uptimecrew/taxcalc-api}"
REGION="${AWS_REGION:-us-east-1}"

# Textbook defaults - see the caveat above. Override once confirmed from a real token:
#   SUBJECT_DEV='repo:ORG@123/REPO@456:environment:dev' \
#   SUBJECT_MAIN='repo:ORG@123/REPO@456:ref:refs/heads/main' \
#   SUBJECT_PROD='repo:ORG@123/REPO@456:environment:prod' \
#     ./scripts/docker-oidc-bootstrap.sh
SUBJECT_DEV="${SUBJECT_DEV:-repo:${REPO}:environment:dev}"
SUBJECT_MAIN="${SUBJECT_MAIN:-repo:${REPO}:ref:refs/heads/main}"
SUBJECT_PROD="${SUBJECT_PROD:-repo:${REPO}:environment:prod}"

BUILD_ROLE="${BUILD_ROLE:-taxcalc-api-build-push}"
PROD_ROLE="${PROD_ROLE:-taxcalc-api-prod-deploy}"

ACCOUNT=$(aws sts get-caller-identity --query 'Account' --output text)
PROVIDER_ARN="arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
ECR_REPO_ARN="arn:aws:ecr:${REGION}:${ACCOUNT}:repository/${ECR_REPO}"

echo "==> account=${ACCOUNT} region=${REGION} ecr-repo=${ECR_REPO}"
echo "==> pinning build sub: ${SUBJECT_DEV} / ${SUBJECT_MAIN}"
echo "==> pinning prod  sub: ${SUBJECT_PROD}"

# 1. The OIDC provider. One per account, shared with the W5 D4 serverless role; creating it
#    twice is an error, so tolerate that.
echo "==> ensuring the GitHub OIDC provider exists"
aws iam create-open-id-connect-provider \
  --url "https://token.actions.githubusercontent.com" \
  --client-id-list "sts.amazonaws.com" \
  --thumbprint-list "6938fd4d98bab03faadb97b34396831e3780aea1" \
  >/dev/null 2>&1 || echo "    (already present)"

create_or_update_role() {
  local role_name="$1" trust_doc="$2" description="$3"
  if aws iam get-role --role-name "${role_name}" >/dev/null 2>&1; then
    aws iam update-assume-role-policy --role-name "${role_name}" --policy-document "${trust_doc}"
  else
    aws iam create-role --role-name "${role_name}" \
      --description "${description}" \
      --assume-role-policy-document "${trust_doc}" >/dev/null
  fi
}

# 2a. taxcalc-api-build-push - the build role. sub pinned to BOTH the dev Environment the job
#     runs under and the main branch ref, belt-and-suspenders (see infra/oidc/README.md).
echo "==> creating/updating role ${BUILD_ROLE}"
BUILD_TRUST=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "${PROVIDER_ARN}" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": { "token.actions.githubusercontent.com:sub": ["${SUBJECT_DEV}", "${SUBJECT_MAIN}"] }
    }
  }]
}
JSON
)
create_or_update_role "${BUILD_ROLE}" "${BUILD_TRUST}" \
  "GitHub Actions OIDC role for taxcalc-api's build+push-to-ECR job (W6 D1)"

# ecr:GetAuthorizationToken has no resource-level permissions in IAM - it must stay
# Resource: "*"; everything else is scoped to the one ECR repository this pipeline owns.
BUILD_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
    { "Effect": "Allow",
      "Action": ["ecr:BatchCheckLayerAvailability", "ecr:CompleteLayerUpload",
                 "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart"],
      "Resource": "${ECR_REPO_ARN}" }
  ]
}
JSON
)
aws iam put-role-policy --role-name "${BUILD_ROLE}" \
  --policy-name "taxcalc-api-build-push" --policy-document "${BUILD_POLICY}"

# 2b. taxcalc-api-prod-deploy - the prod role. sub pinned to environment:prod ONLY (no branch
#     condition - the Environment's required reviewers are the real gate; see
#     infra/oidc/README.md for why a ref condition here would be theatre).
echo "==> creating/updating role ${PROD_ROLE}"
PROD_TRUST=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "${PROVIDER_ARN}" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "${SUBJECT_PROD}"
      }
    }
  }]
}
JSON
)
create_or_update_role "${PROD_ROLE}" "${PROD_TRUST}" \
  "GitHub Actions OIDC role for taxcalc-api's manual prod-promote workflow (W6 D1)"

# Least-privilege for TODAY's deploy-prod.yml: it only confirms the image exists in ECR. W6 D3
# widens this policy when the real kubectl rollout / sam deploy step replaces the placeholder.
PROD_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "ecr:DescribeImages", "Resource": "${ECR_REPO_ARN}" }
  ]
}
JSON
)
aws iam put-role-policy --role-name "${PROD_ROLE}" \
  --policy-name "taxcalc-api-prod-deploy" --policy-document "${PROD_POLICY}"

BUILD_ARN=$(aws iam get-role --role-name "${BUILD_ROLE}" --query 'Role.Arn' --output text)
PROD_ARN=$(aws iam get-role --role-name "${PROD_ROLE}" --query 'Role.Arn' --output text)

echo ""
echo "==> ensuring ECR repository ${ECR_REPO} exists (immutable tags, scan-on-push)"
aws ecr create-repository \
  --repository-name "${ECR_REPO}" \
  --region "${REGION}" \
  --image-tag-mutability IMMUTABLE \
  --image-scanning-configuration scanOnPush=true \
  >/dev/null 2>&1 || echo "    (already present)"

echo ""
echo "=============================================================================="
echo " Build role: ${BUILD_ARN}"
echo " Prod role:  ${PROD_ARN}"
echo ""
echo " Wire these into the repo as VARIABLES (not secrets - an account id/region"
echo " is not confidential, and hiding it only makes a failed role-to-assume"
echo " harder to read - same rationale scripts/oidc-bootstrap.sh gives):"
echo ""
echo "   gh variable set AWS_ACCOUNT_ID --body '${ACCOUNT}'"
echo "   gh variable set AWS_REGION --body '${REGION}'"
echo "=============================================================================="
