#!/usr/bin/env bash
# scripts/observability-apply.sh - one-shot apply of the W5 D5 observability layer.
#
# Idempotent: safe to re-run after any edit. Order matters and is deliberate - everything that
# can fail WITHOUT touching the cluster (Sloth drift, promtool, dashboard JSON) is checked first,
# so a bad rule never reaches Prometheus and a half-applied state is not a normal outcome.
#
# Prerequisite: scripts/observability-bootstrap.sh has installed the PLG-T stack once.
set -euo pipefail

NS="${NS:-taxcalc-dev}"
OBS_NS="${OBS_NS:-monitoring}"
APP="${APP:-taxcalc-api}"
SLOTH_SPEC="slo/taxcalc-api.sloth.yaml"
RULE_FILE="manifests/observability/${APP}-prometheusrule.yaml"
DASH_JSON=".grafana/dashboards/${APP}-red.json"
PROM_IMAGE="prom/prometheus:v2.54.1"
# Overridable so CI can fail fast (see .github/workflows/observability.yml); locally the longer
# default absorbs a cold JVM start on a laptop that is also running the whole PLG-T stack.
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-300s}"
# Sloth runs from a pinned CONTAINER, not from whatever `sloth` is on PATH. It stamps its own
# version into the generated file (a `sloth_version` label and the header comment), and the
# version string differs by distribution - Homebrew's sloth-cli reports "0.16.0" where the
# GitHub release binary reports "v0.16.0". Two developers with the same Sloth version would
# therefore produce two different files and the drift gate would fail for nobody's mistake.
# Caught by CI on the first push: the gate flagged a diff whose only content was that stamp.
SLOTH_IMAGE="ghcr.io/slok/sloth:v0.16.0"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# 1. The PLG-T stack has to exist first: everything below either registers with the Prometheus
#    Operator or is scraped/collected by something in this namespace.
echo "[1/6] preflight: monitoring namespace"
kubectl get ns "${OBS_NS}" >/dev/null

# 2. Regenerate the PrometheusRule from the Sloth spec and fail on ANY drift. The generated file
#    is committed, so this is the local half of the same gate CI runs: the rule and the spec that
#    is supposed to explain it can never disagree.
echo "[2/6] sloth generate + drift check"
docker run --rm -u "$(id -u):$(id -g)" -v "${PWD}:/w" -w /w "${SLOTH_IMAGE}" \
  generate -i "${SLOTH_SPEC}" -o "${RULE_FILE}" >/dev/null 2>&1
if ! git diff --quiet -- "${RULE_FILE}"; then
  echo "ERROR: ${RULE_FILE} drifted from ${SLOTH_SPEC}. Re-commit the regenerated file." >&2
  git --no-pager diff -- "${RULE_FILE}" >&2
  exit 1
fi

# 3. promtool BEFORE apply. The Operator's admission webhook would also reject malformed PromQL,
#    but only once the object is already being applied and with a far worse message; this fails
#    the same mistake a step earlier, locally, in a second.
#
#    promtool checks a Prometheus rule FILE, not a Kubernetes PrometheusRule object, so the CR's
#    `spec:` has to be unwrapped and de-indented first - feeding it the whole manifest fails with
#    a misleading "field groups not found" that reads like the rules are missing.
echo "[3/6] promtool check rules + alert unit tests"
# Full template rather than `mktemp -t taxcalc-rules`: BSD mktemp (macOS) treats the argument as
# a prefix and appends its own suffix, but GNU mktemp (the CI runner) requires the X's and fails
# outright with "too few X's in template" - which is how this first ran green locally and red in
# CI.
UNWRAPPED="$(mktemp "${TMPDIR:-/tmp}/taxcalc-rules.XXXXXX")"
trap 'rm -f "${UNWRAPPED}"' EXIT
python3 - "${RULE_FILE}" > "${UNWRAPPED}" <<'PY'
import sys
text = open(sys.argv[1]).read()
spec = text[text.index("spec:") + len("spec:"):]
print("\n".join(line[2:] if line.startswith("  ") else line for line in spec.splitlines()))
PY
# mktemp creates the file 0600 owned by the invoking user, and the prom/prometheus image runs as
# `nobody` - so the bind mount is readable on a Docker Desktop VM (which loosens permissions
# across the file share) and "permission denied" on a Linux runner, where the mount is the real
# file. World-readable is correct here: this is a rule file derived from a committed manifest,
# not a secret.
chmod 0644 "${UNWRAPPED}"
docker run --rm -v "${UNWRAPPED}:/rules.yaml" --entrypoint promtool "${PROM_IMAGE}" \
  check rules /rules.yaml
# `check rules` only proves the PromQL parses. The unit tests prove the alerts actually fire at
# the burn rates they claim to - including the case that caught a real defect in this SLI, where
# a total outage produced NO alert because `total - good` returns an empty vector when nothing
# matches the good selector.
docker run --rm -v "${UNWRAPPED}:/rules.yaml" \
  -v "${PWD}/slo/taxcalc-api.rules_test.yaml:/test.yaml" \
  --entrypoint promtool "${PROM_IMAGE}" test rules /test.yaml

# 4. The dashboard ConfigMap is rebuilt from the JSON in git on every apply, rather than the JSON
#    being hand-pasted into the manifest. Two copies of a 100-line JSON document is two sources of
#    truth, and the one nobody looks at is the one that is deployed.
echo "[4/6] dashboard ConfigMap from ${DASH_JSON}"
python3 -c "import json,sys; json.load(open('${DASH_JSON}'))"   # fail early on malformed JSON
# The manifest goes FIRST and the generated ConfigMap second, because that file's first document
# is a same-named placeholder: applying it after the real dashboard would overwrite a working
# dashboard with an empty one on every run - and the failure is invisible until someone opens
# Grafana. The manifest's second document (the provisioning provider) is what is actually wanted
# from it here.
kubectl apply -f "manifests/observability/${APP}-dashboard-configmap.yaml" >/dev/null
kubectl -n "${OBS_NS}" create configmap "${APP}-grafana-dashboard" \
  --from-file="${APP}-red.json=${DASH_JSON}" \
  --dry-run=client -o yaml \
  | kubectl label --local -f - --dry-run=client -o yaml \
      grafana_dashboard=1 "app.kubernetes.io/name=${APP}" "app.kubernetes.io/part-of=taxcalc" \
  | kubectl apply -f -

# 5. The application-facing objects.
echo "[5/6] apply ServiceMonitor / Deployment patch / PrometheusRule / AlertmanagerConfig"
kubectl apply -n "${NS}" -f "manifests/observability/${APP}-servicemonitor.yaml"
kubectl patch -n "${NS}" deployment "${APP}" \
  --patch-file "manifests/observability/${APP}-deployment-patch.yaml"
kubectl apply -n "${NS}" -f "${RULE_FILE}"
kubectl apply -n "${NS}" -f "manifests/observability/${APP}-alertmanagerconfig.yaml"

# 6. The patch triggers a new ReplicaSet; block until it is actually serving, so a caller that
#    runs the smoke script next is not racing the rollout.
echo "[6/6] rollout"
kubectl -n "${NS}" rollout status "deployment/${APP}" --timeout="${ROLLOUT_TIMEOUT}"

echo "OK: ${APP} is now scraped, traced, and alarming."
