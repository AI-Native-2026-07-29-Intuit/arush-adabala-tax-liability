#!/usr/bin/env bash
# scripts/loadtest-token.sh - mint the JWTs the k6 load test carries, and publish the public key
# the api validates them with (W6 D5 Task 3).
#
# WHY THIS EXISTS AT ALL
#
# Every route worth load-testing on this service is authenticated, and there is no identity
# provider to get a token from. The base profile points
# spring.security.oauth2.resourceserver.jwt.issuer-uri at
# https://idp.example.internal/realms/uptimecrew, which is and always has been a placeholder -
# nothing is behind it. Unit and integration tests sidestep that with spring-security-test's mock
# JwtDecoder, which is a test-classpath facility a running pod cannot use.
#
# So a load generator pointed at a real pod gets 401 on every request. That does not merely
# invalidate the latency numbers; it INVERTS the gate's meaning. A 401 is fast and cheap, so p99
# looks superb, http_req_failed pins at 1.0, and the cost Trend reads 0 because an unauthorised
# request never reaches the cost path. The prettiest latency graph this repo can produce is the
# one where nothing worked.
#
# The fix is a real keypair and real RS256 tokens, verified by the api the same way a production
# token would be - same filter chain, same converter, same authorities. Only the KEY SOURCE
# differs (a mounted public key instead of a JWKS fetch), which is the one thing an absent IdP
# makes impossible.
#
# WHAT IS AND IS NOT A SECRET HERE
#
# The PRIVATE key is generated into a gitignored scratch directory, is never committed, never
# enters an image, and never leaves this machine. The PUBLIC key goes into a Kubernetes Secret
# the api mounts - it is public by definition, and lives in a Secret rather than a ConfigMap only
# because it is mounted next to credential material and consistency is cheaper than explaining
# the exception.
#
# The tokens are short-lived (default 2h, enough for a long k6 run and not enough to be worth
# keeping) and are written to a gitignored file k6 reads at startup.
#
# WHY MANY TOKENS AND NOT ONE
#
# RateLimitFilter buckets by JWT SUBJECT at 10 requests/minute on LLM routes. One token means one
# bucket means every LLM request after the tenth in a minute is a 429 - across 200 VUs that is
# effectively a 100% failure rate on that slice. Distinct subjects model what the rate limiter is
# actually for (per-caller fairness) rather than defeating it: each synthetic caller gets its own
# bucket and stays inside the same limit a real caller would.
set -euo pipefail

# kubectl is only needed to publish the public key. In MINT_ONLY mode (CI) there is no cluster,
# and sourcing the preflight would exit 1 on a runner that correctly has no kubectl at all.
if [ "${MINT_ONLY:-0}" != "1" ]; then
  . "$(dirname "${BASH_SOURCE[0]}")/lib/kube-preflight.sh"
else
  # The preflight is what sets KUBECTL, and skipping it leaves the variable unset - which under
  # `set -u` makes the closing "Next:" message (an unquoted heredoc that interpolates ${KUBECTL})
  # a fatal error. That killed the CI run AFTER minting had completely succeeded: keypair
  # generated, 200 tokens written, exit 1. A script that does all of its work and then fails on
  # its own help text is the worst kind of red, because every artefact it was supposed to produce
  # is sitting right there on disk.
  #
  # Defaulted rather than blanked so the printed command stays copy-pasteable for the one reader
  # who runs MINT_ONLY=1 by hand and then does go on to talk to a cluster.
  KUBECTL="${KUBECTL:-kubectl}"
fi

NS="${NS:-taxcalc-dev}"
COUNT="${COUNT:-200}"           # one token per VU at the k6 script's peak
TTL_SECONDS="${TTL_SECONDS:-7200}"
TENANT="${TENANT:-tenant-synth}"
OUT_DIR="${OUT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.loadtest}"
SECRET_NAME="${SECRET_NAME:-taxcalc-loadtest-jwt}"

mkdir -p "${OUT_DIR}"
chmod 700 "${OUT_DIR}"

PRIVATE_KEY="${OUT_DIR}/private.pem"
PUBLIC_KEY="${OUT_DIR}/public.pem"
TOKENS_FILE="${OUT_DIR}/tokens.json"

# Reuse an existing keypair if one is present, so re-minting tokens does not invalidate a Secret
# already mounted into running pods - rotating the key means restarting every api pod, and doing
# that silently on every run would make a mid-test token refresh look like a mass 401 incident.
if [ ! -f "${PRIVATE_KEY}" ]; then
  echo "==> generating a fresh RS256 keypair in ${OUT_DIR}"
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${PRIVATE_KEY}" 2>/dev/null
  chmod 600 "${PRIVATE_KEY}"
  openssl rsa -in "${PRIVATE_KEY}" -pubout -out "${PUBLIC_KEY}" 2>/dev/null
else
  echo "==> reusing the existing keypair in ${OUT_DIR}"
fi

# Derive the public half if it is missing. CI supplies ONLY the private key (as a repository
# secret); deriving rather than storing both is what keeps the secret to one value, and a public
# key that is always computed from the private one cannot drift out of step with it.
if [ ! -f "${PUBLIC_KEY}" ]; then
  openssl rsa -in "${PRIVATE_KEY}" -pubout -out "${PUBLIC_KEY}" 2>/dev/null
fi

# base64url: standard base64 with +/ swapped for -_ and padding stripped. Getting this wrong
# produces a token the server rejects as malformed, which is indistinguishable from a signature
# failure in the logs.
b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

now=$(date -u +%s)
exp=$((now + TTL_SECONDS))

# RS256, and the `alg` is asserted rather than left to the verifier. Spring Security rejects
# `alg: none` outright, but stating RS256 here keeps the token honest about what signed it.
header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)

echo "==> minting ${COUNT} tokens (exp in ${TTL_SECONDS}s, tenant=${TENANT})"
printf '[' > "${TOKENS_FILE}"
for i in $(seq 1 "${COUNT}"); do
  # scope + roles match ScopeAndRoleAuthoritiesConverter exactly: the `scope` claim is
  # space-delimited and becomes SCOPE_* authorities, the `roles` claim is a string list and
  # becomes ROLE_* authorities. TaxpayerController's @PreAuthorize needs BOTH, and a token
  # carrying only one produces a 403 that reads like a missing role when it may be the scope.
  #
  # taxpayers.write is included so the k6 write slice can POST; the read routes need only
  # taxpayers.read.
  payload=$(printf '{"sub":"loadtest-vu-%s","scope":"taxpayers.read taxpayers.write","roles":["TAXPAYER_READER","TAXPAYER_WRITER"],"tenant":"%s","iat":%s,"exp":%s}' \
    "${i}" "${TENANT}" "${now}" "${exp}" | b64url)
  signing_input="${header}.${payload}"
  signature=$(printf '%s' "${signing_input}" \
    | openssl dgst -sha256 -sign "${PRIVATE_KEY}" -binary \
    | b64url)
  [ "${i}" -gt 1 ] && printf ',' >> "${TOKENS_FILE}"
  printf '"%s.%s"' "${signing_input}" "${signature}" >> "${TOKENS_FILE}"
done
printf ']' >> "${TOKENS_FILE}"
chmod 600 "${TOKENS_FILE}"
echo "    wrote ${TOKENS_FILE}"

# MINT_ONLY=1 skips the cluster write. CI needs tokens but has no cluster credentials and does
# not need any - the public half is already mounted in the cluster from a previous local run, and
# the workflow supplies the SAME private key through a repository secret so the signatures still
# verify against it. Requiring kubectl in CI would mean giving the load-test job standing cluster
# access to publish a key that is, by construction, public.
if [ "${MINT_ONLY:-0}" = "1" ]; then
  echo "==> MINT_ONLY=1: skipping the secret/${SECRET_NAME} write"
else
  echo "==> publishing the PUBLIC key into secret/${SECRET_NAME} in ${NS}"
  $KUBECTL -n "${NS}" create secret generic "${SECRET_NAME}" \
    --from-file=public.pem="${PUBLIC_KEY}" \
    --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
  echo "    secret/${SECRET_NAME} applied"
fi

cat <<MSG

Next:
  1. Put the api into the loadtest profile (mounts the key, swaps in SyntheticChatUpstream):
       $KUBECTL -n ${NS} apply -f <(kubectl kustomize overlays/loadtest)   # in the config repo
  2. Run k6:
       k6 run -e TARGET=http://localhost:8080 -e TOKENS=${TOKENS_FILE} loadtests/taxcalc-api-p99.js

The private key stays in ${OUT_DIR} and is gitignored. Delete that directory to rotate.
MSG
