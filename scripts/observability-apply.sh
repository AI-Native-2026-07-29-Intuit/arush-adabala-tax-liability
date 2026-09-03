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

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# 1. The PLG-T stack has to exist first: everything below either registers with the Prometheus
#    Operator or is scraped/collected by something in this namespace.
echo "[1/6] preflight: monitoring namespace"
kubectl get ns "${OBS_NS}" >/dev/null

# 2. Regenerate the PrometheusRule from the Sloth spec and fail on ANY drift. The generated file
#    is committed, so this is the local half of the same gate CI runs: the rule and the spec that
#    is supposed to explain it can never disagree.
echo "[2/6] sloth generate + drift check"
sloth generate -i "${SLOTH_SPEC}" -o "${RULE_FILE}" >/dev/null 2>&1
if ! git diff --quiet -- "${RULE_FILE}"; then
  echo "ERROR: ${RULE_FILE} drifted from ${SLOTH_SPEC}. Re-commit the regenerated file." >&2
  git --no-pager diff -- "${RULE_FILE}" >&2
  exit 1
fi

# 3. promtool BEFORE apply. The Operator's own admission webhook is disabled in this cluster
#    (see the Helm values), so this is the only thing standing between a typo in PromQL and a
#    rule group Prometheus loads as broken.
#
#    promtool checks a Prometheus rule FILE, not a Kubernetes PrometheusRule object, so the CR's
#    `spec:` has to be unwrapped and de-indented first - feeding it the whole manifest fails with
#    a misleading "field groups not found" that reads like the rules are missing.
echo "[3/6] promtool check rules"
python3 - "${RULE_FILE}" <<'PY' | docker run --rm -i --entrypoint promtool "${PROM_IMAGE}" check rules /dev/stdin
import sys
text = open(sys.argv[1]).read()
spec = text[text.index("spec:") + len("spec:"):]
print("\n".join(line[2:] if line.startswith("  ") else line for line in spec.splitlines()))
PY

# 4. The dashboard ConfigMap is rebuilt from the JSON in git on every apply, rather than the JSON
#    being hand-pasted into the manifest. Two copies of a 100-line JSON document is two sources of
#    truth, and the one nobody looks at is the one that is deployed.
echo "[4/6] dashboard ConfigMap from ${DASH_JSON}"
python3 -c "import json,sys; json.load(open('${DASH_JSON}'))"   # fail early on malformed JSON
kubectl -n "${OBS_NS}" create configmap "${APP}-grafana-dashboard" \
  --from-file="${APP}-red.json=${DASH_JSON}" \
  --dry-run=client -o yaml \
  | kubectl label --local -f - --dry-run=client -o yaml \
      grafana_dashboard=1 "app.kubernetes.io/name=${APP}" "app.kubernetes.io/part-of=taxcalc" \
  | kubectl apply -f -
# The provisioning ConfigMap (second document in that file) is applied as-is; the dashboard
# document in it is a placeholder that the create above has just overwritten.
kubectl apply -f "manifests/observability/${APP}-dashboard-configmap.yaml" >/dev/null

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
kubectl -n "${NS}" rollout status "deployment/${APP}" --timeout=300s

echo "OK: ${APP} is now scraped, traced, and alarming."
