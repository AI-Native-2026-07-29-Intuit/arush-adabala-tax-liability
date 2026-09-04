#!/usr/bin/env bash
# scripts/docker-oidc-verify.sh - assert that scripts/docker-oidc-bootstrap.sh actually
# produced the account-side resources W6 D1 Task 3 asks for, and report honestly on the
# ones this check cannot reach.
#
# Run it against a real AWS dev account:
#   AWS_PROFILE=dev ./scripts/docker-oidc-verify.sh
#
# Or against the floci emulator, the same way scripts/oidc-bootstrap.sh documents:
#   docker run -d --name floci -p 4566:4566 -v /var/run/docker.sock:/var/run/docker.sock \
#     floci/floci:latest
#   AWS_ENDPOINT_URL=http://localhost:4566 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \
#     ./scripts/docker-oidc-bootstrap.sh && ./scripts/docker-oidc-verify.sh
#
# WHAT THE EMULATOR RUN PROVES: the provider, both roles, the inline policy and the ECR
# repository are created exactly as the bootstrap script writes them, and the trust policy
# AWS stores back is byte-for-byte the one infra/oidc/ commits - i.e. the documents are
# well-formed, accepted by the real IAM API shape, and pinned to the right subject.
#
# WHAT IT DOES NOT PROVE, and no emulator can:
#   1. That GitHub can assume the role. MEASURED 2026-09-03, not assumed: floci was handed a
#      deliberately FORGED web-identity token - unsigned, `iss` of https://evil.example.com,
#      and a `sub` of repo:someone-else/their-repo:ref:refs/heads/main - and it returned
#      working credentials for taxcalc-api-build-push anyway:
#
#        "Arn": "arn:aws:sts::000000000000:assumed-role/taxcalc-api-build-push/..."
#        "SubjectFromWebIdentityToken": "web-identity-subject"   <- placeholder; never parsed
#        "Provider": "accounts.google.com"                       <- not even GitHub
#
#      That ARN is precisely the string Task 3's Done-When tells you to look for in the run
#      log. So pointing CI at an emulator would print a convincing "Authenticated as
#      arn:aws:sts::...:assumed-role/taxcalc-api-build-push/..." with the trust policy
#      DELETED, or pinned to another repo entirely - manufacturing the evidence instead of
#      producing it. Never wire the deploy path at an emulator to make this check go green.
#      The real question is answered instead by scripts/oidc-trust-simulate.py, which
#      reproduces IAM's decision procedure against the real token claims measured by
#      .github/workflows/oidc-probe.yml.
#   2. ECR tag immutability. Measured 2026-09-04: floci backs ECR with a plain `registry:2`
#      container, so `docker push` bypasses the ECR control plane entirely. A re-push of
#      DIFFERENT content to an already-existing SHA tag SUCCEEDED against floci, where real
#      ECR rejects it with ImageTagAlreadyExistsException. The repository's configured
#      mutability is checked below; its enforcement is not, and cannot be, here.
#   3. `aws ecr describe-images`. Same cause - images pushed to floci's data-plane registry
#      are invisible to its control-plane API, which returns [] however many were pushed.
set -euo pipefail

ECR_REPO="${ECR_REPO:-uptimecrew/taxcalc-api}"
REGION="${AWS_REGION:-us-east-1}"
BUILD_ROLE="${BUILD_ROLE:-taxcalc-api-build-push}"
PROD_ROLE="${PROD_ROLE:-taxcalc-api-prod-deploy}"

# Must match scripts/docker-oidc-bootstrap.sh. Measured from a real token by
# .github/workflows/oidc-probe.yml on 2026-09-04 - not the textbook repo:<org>/<repo> form.
OIDC_SUBJECT_PREFIX="${OIDC_SUBJECT_PREFIX:-repo:AI-Native-2026-07-29-Intuit@309728071/arush-adabala-tax-liability@1317703842}"

PASS=0
FAIL=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL + 1)); }
# eq <actual> <expected> <label> - reports the actual value on failure, so a FAIL line is
# diagnostic on its own rather than sending you back to the AWS CLI to find out what it got.
eq() {
  if [[ "$1" == "$2" ]]; then
    ok "$3"
  else
    bad "$3 -- got '$1', expected '$2'"
  fi
}
# Compare two JSON documents as values, so key order and whitespace never cause a false FAIL.
same_json() { [[ "$(jq -Sc . <<<"$1")" == "$(jq -Sc . <<<"$2")" ]]; }

ACCOUNT=$(aws sts get-caller-identity --query 'Account' --output text)
TARGET="real AWS account"
[[ -n "${AWS_ENDPOINT_URL:-}" ]] && TARGET="emulator at ${AWS_ENDPOINT_URL}"
ECR_REPO_ARN="arn:aws:ecr:${REGION}:${ACCOUNT}:repository/${ECR_REPO}"

echo "==> verifying against ${TARGET} (account=${ACCOUNT} region=${REGION})"
echo ""

# ---------------------------------------------------------------- 1. OIDC provider
echo "[1] GitHub OIDC provider"
if aws iam list-open-id-connect-providers \
     --query 'OpenIDConnectProviderList[].Arn' --output text \
     | grep -q 'token.actions.githubusercontent.com'; then
  ok "token.actions.githubusercontent.com provider exists"
else
  bad "no token.actions.githubusercontent.com provider in this account"
fi

# ------------------------------------------------- 2. build role trust policy (Done-when)
# The literal Done-when: "aws iam get-role --role-name taxcalc-api-build-push shows sub
# condition pinned to repo + dev + main".
echo ""
echo "[2] ${BUILD_ROLE} trust policy - sub pinned to repo + dev + main"
if TRUST=$(aws iam get-role --role-name "${BUILD_ROLE}" \
             --query 'Role.AssumeRolePolicyDocument' --output json 2>/dev/null); then
  ok "role ${BUILD_ROLE} exists"

  AUD=$(jq -r '.Statement[0].Condition.StringEquals."token.actions.githubusercontent.com:aud"' <<<"${TRUST}")
  eq "${AUD}" "sts.amazonaws.com" "aud == sts.amazonaws.com"

  SUBS=$(jq -r '[.Statement[0].Condition.StringLike."token.actions.githubusercontent.com:sub"] | flatten | .[]' <<<"${TRUST}")
  for suffix in "environment:dev" "ref:refs/heads/main"; do
    if grep -Fxq "${OIDC_SUBJECT_PREFIX}:${suffix}" <<<"${SUBS}"; then
      ok "sub pins :${suffix}"
    else
      bad "sub does NOT pin ${OIDC_SUBJECT_PREFIX}:${suffix}"
    fi
  done

  # A trust policy that pins the right two subjects but ALSO allows a third is not pinned.
  COUNT=$(grep -c . <<<"${SUBS}")
  eq "${COUNT}" "2" "sub list has exactly 2 entries - no third repo/branch/environment can assume"

  # The committed artefact must be the thing that was actually applied, modulo the account
  # id placeholder infra/oidc/README.md documents. Otherwise infra/oidc/ is decoration.
  COMMITTED=$(sed "s/123456789012/${ACCOUNT}/g" infra/oidc/trust-policy-build.json \
              | jq 'del(.Statement[].Sid)')
  if same_json "${TRUST}" "${COMMITTED}"; then
    ok "applied policy matches infra/oidc/trust-policy-build.json"
  else
    bad "applied policy DIFFERS from infra/oidc/trust-policy-build.json"
  fi
else
  bad "role ${BUILD_ROLE} does not exist - run scripts/docker-oidc-bootstrap.sh first"
fi

# ------------------------------------------------------------ 3. least-privilege inline policy
echo ""
echo "[3] ${BUILD_ROLE} inline policy - least privilege, correctly scoped"
if POLICY=$(aws iam get-role-policy --role-name "${BUILD_ROLE}" \
              --policy-name "${BUILD_ROLE}" --query 'PolicyDocument' --output json 2>/dev/null); then
  EXPECTED='{"Version":"2012-10-17","Statement":[
    {"Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
    {"Effect":"Allow","Action":["ecr:BatchCheckLayerAvailability","ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload","ecr:PutImage","ecr:UploadLayerPart"],
     "Resource":"'"${ECR_REPO_ARN}"'"}]}'
  if same_json "${POLICY}" "${EXPECTED}"; then
    ok "exactly the 6 required actions; GetAuthorizationToken on *, the other 5 on ${ECR_REPO}"
  else
    bad "inline policy is not the expected least-privilege document; got:"
    jq -S . <<<"${POLICY}" | sed 's/^/        /'
  fi
else
  bad "no inline policy named ${BUILD_ROLE} on role ${BUILD_ROLE}"
fi

# ------------------------------------------------------------------------ 4. ECR repository
echo ""
echo "[4] ECR repository ${ECR_REPO}"
if REPO_JSON=$(aws ecr describe-repositories --repository-names "${ECR_REPO}" \
                 --region "${REGION}" --query 'repositories[0]' --output json 2>/dev/null); then
  ok "repository exists"

  SCAN=$(jq -r '.imageScanningConfiguration.scanOnPush' <<<"${REPO_JSON}")
  eq "${SCAN}" "true" "scanOnPush enabled - ECR's own scan is the second pair of eyes after Trivy"

  # IMMUTABLE_WITH_EXCLUSION, not plain IMMUTABLE, and deliberately so: _build-and-push.yml
  # writes a floating `main` tag on every build, which a plain IMMUTABLE repo rejects on the
  # SECOND main build. See docker-oidc-bootstrap.sh's comment at the create-repository call.
  # NOTE this asserts the repository's CONFIGURATION only. Against floci it does not assert
  # enforcement - see the header: a re-push to an existing SHA tag succeeds there.
  MUT=$(jq -r '.imageTagMutability' <<<"${REPO_JSON}")
  eq "${MUT}" "${ECR_TAG_MUTABILITY:-IMMUTABLE_WITH_EXCLUSION}" \
     "imageTagMutability - SHA tags immutable, :main alone may move"
else
  bad "repository ${ECR_REPO} does not exist"
fi

# ------------------------------------------------------------------- 5. prod role (Task 4)
echo ""
echo "[5] ${PROD_ROLE} trust policy - sub pinned to environment:prod only"
if PROD_TRUST=$(aws iam get-role --role-name "${PROD_ROLE}" \
                  --query 'Role.AssumeRolePolicyDocument' --output json 2>/dev/null); then
  ok "role ${PROD_ROLE} exists"
  PROD_SUB=$(jq -r '.Statement[0].Condition.StringEquals."token.actions.githubusercontent.com:sub"' <<<"${PROD_TRUST}")
  eq "${PROD_SUB}" "${OIDC_SUBJECT_PREFIX}:environment:prod" \
     "sub pins :environment:prod (no branch condition - reviewers are the real gate)"
else
  bad "role ${PROD_ROLE} does not exist"
fi

# ------------------------------------------------------------------------------- verdict
echo ""
echo "=============================================================================="
printf ' %d passed, %d failed\n' "${PASS}" "${FAIL}"
if [[ -n "${AWS_ENDPOINT_URL:-}" ]]; then
  cat <<'NOTE'

 EMULATOR RUN - three Task 3 checks remain genuinely unanswered here:
   - whether GitHub can actually assume the role  -> scripts/oidc-trust-simulate.py
   - whether ECR enforces SHA-tag immutability    -> needs a real account
   - `aws ecr describe-images` listing the SHA tag -> needs a real account
 Do NOT `gh variable set AWS_ACCOUNT_ID` to this emulator's account id: that would
 un-gate _build-and-push.yml's ECR steps in CI, and a GitHub runner cannot reach a
 floci on your laptop - main would fail at the assume-role step.
NOTE
fi
echo "=============================================================================="
[[ "${FAIL}" -eq 0 ]]
