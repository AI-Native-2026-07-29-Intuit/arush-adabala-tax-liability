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
RULES_FILE="slo/taxcalc-api.rules.yaml"
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
echo "[1/7] preflight: monitoring namespace"
kubectl get ns "${OBS_NS}" >/dev/null

# 2. Re-render BOTH artefacts from the Sloth spec and fail on ANY drift. scripts/slo-render.sh
#    writes the PrometheusRule CR (what kubectl applies) and the flat rule file (what promtool
#    reads) from one spec, so the two can never disagree and neither is ever hand-edited. This is
#    the local half of the same gate CI runs.
echo "[2/7] render SLO artefacts + drift check"
./scripts/slo-render.sh >/dev/null
if ! git diff --quiet -- "${RULE_FILE}" "${RULES_FILE}"; then
  echo "ERROR: ${RULE_FILE} / ${RULES_FILE} drifted from ${SLOTH_SPEC}. Re-commit the rendered files." >&2
  git --no-pager diff -- "${RULE_FILE}" "${RULES_FILE}" >&2
  exit 1
fi

# 3. promtool BEFORE apply, run directly against the committed rule file - no unwrapping, no
#    temp files. `check rules` proves the PromQL parses; the unit tests prove the alerts fire at
#    the burn rates they claim to, including the total-outage case that once produced NO alert at
#    all because `total - good` returns an empty vector when nothing matches the good selector.
echo "[3/7] promtool check rules + alert unit tests"
docker run --rm -v "${PWD}/${RULES_FILE}:/rules.yaml" --entrypoint promtool "${PROM_IMAGE}" \
  check rules /rules.yaml
docker run --rm -v "${PWD}/${RULES_FILE}:/rules.yaml" \
  -v "${PWD}/slo/taxcalc-api.rules_test.yaml:/test.yaml" \
  --entrypoint promtool "${PROM_IMAGE}" test rules /test.yaml

# 4. The dashboard ConfigMap is rebuilt from the JSON in git on every apply, rather than the JSON
#    being hand-pasted into the manifest. Two copies of a 100-line JSON document is two sources of
#    truth, and the one nobody looks at is the one that is deployed.
echo "[4/7] dashboard ConfigMap from ${DASH_JSON}"
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
echo "[5/7] apply ServiceMonitor / Deployment patch / PrometheusRule / AlertmanagerConfig"
kubectl apply -n "${NS}" -f "manifests/observability/${APP}-servicemonitor.yaml"
kubectl patch -n "${NS}" deployment "${APP}" \
  --patch-file "manifests/observability/${APP}-deployment-patch.yaml"
kubectl apply -n "${NS}" -f "${RULE_FILE}"
kubectl apply -n "${NS}" -f "manifests/observability/${APP}-alertmanagerconfig.yaml"

# 6. The patch triggers a new ReplicaSet; block until it is actually serving, so a caller that
#    runs the smoke script next is not racing the rollout.
echo "[6/7] rollout"
kubectl -n "${NS}" rollout status "deployment/${APP}" --timeout="${ROLLOUT_TIMEOUT}"

# The Operator's reconciliation state. Note WHERE this is read from: a PrometheusRule has no
# status subresource in this Operator version (v0.77.1 exposes exactly one feature gate, and
# status for configuration resources is not it), so there is no per-rule condition to read. The
# condition that does exist - and that actually gates whether any rule is live - is on the
# Prometheus CR: it flips to Reconciled=True once the Operator has rebuilt the rule files and
# reloaded Prometheus with them.
echo "[7/7] operator reconciliation"
kubectl -n "${OBS_NS}" get prometheus kube-prometheus-stack-prometheus \
  -o jsonpath='{range .status.conditions[*]}  {.type}={.status} ({.reason}){"\n"}{end}'

echo "OK: ${APP} is now scraped, traced, and alarming."
