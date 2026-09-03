#!/usr/bin/env bash
# scripts/slo-render.sh - render the SLO spec into the two artefacts that consume it.
#
# ONE source of truth (slo/taxcalc-api.sloth.yaml), two machine-generated renderings, both
# committed and both drift-gated:
#
#   manifests/observability/taxcalc-api-prometheusrule.yaml  the PrometheusRule CR kubectl applies
#   slo/taxcalc-api.rules.yaml                               the bare rule file promtool reads
#
# The second exists because those two tools cannot read the same file. `kubectl apply` needs a
# Kubernetes object; `promtool check rules` needs a Prometheus rule file and rejects the CR
# outright ("field apiVersion not found in type rulefmt.RuleGroups") with no lenient mode -
# `--lint=none` disables linting, not parsing. Deriving the rule file mechanically from the CR
# keeps them from ever disagreeing: nobody edits either one, CI regenerates both and diffs.
#
# The renderer also gives the two burn-rate alerts distinct NAMES (…BurnFast / …BurnSlow) rather
# than one name split by a `severity` label. Sloth emits a single name per SLO and has no
# per-alert name field, so this is applied here, in code, rather than by hand - which is the
# difference between a build step and the drift the gate exists to catch. It is worth doing on
# its own merits: an alert name is what appears in a pager notification, a silence, and a runbook
# title, and with one shared name a silence on the slow burn also silences the page.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

SLOTH_IMAGE="${SLOTH_IMAGE:-ghcr.io/slok/sloth:v0.16.0}"
SPEC="slo/taxcalc-api.sloth.yaml"
CR="manifests/observability/taxcalc-api-prometheusrule.yaml"
RULES="slo/taxcalc-api.rules.yaml"

docker run --rm -u "$(id -u):$(id -g)" -v "${PWD}:/w" -w /w "${SLOTH_IMAGE}" \
  generate -i "${SPEC}" -o "${CR}" >/dev/null 2>&1

python3 - "${CR}" "${RULES}" <<'PY'
import sys

cr_path, rules_path = sys.argv[1], sys.argv[2]
lines = open(cr_path).read().splitlines()

# --- 1. Give each burn-rate alert its own name -------------------------------------------------
# Sloth writes both alerts with the same `- alert:` value and separates them by a severity label
# further down the block, so each rule has to be read as a block before it can be renamed. Done
# with plain text rather than a YAML round-trip on purpose: a YAML load/dump would reflow and
# reorder the whole generated file, and the drift gate compares bytes.
ALERT_PREFIX = "    - alert: "
blocks, current = [], None
for i, line in enumerate(lines):
    if line.startswith(ALERT_PREFIX):
        if current is not None:
            blocks.append((current, i))
        current = i
    elif current is not None and line and not line.startswith("      "):
        blocks.append((current, i))
        current = None
if current is not None:
    blocks.append((current, len(lines)))

for start, end in blocks:
    body = "\n".join(lines[start:end])
    if "severity: page" in body:
        suffix = "Fast"
    elif "severity: ticket" in body:
        suffix = "Slow"
    else:
        continue
    base = lines[start][len(ALERT_PREFIX):].strip()
    if not base.endswith(suffix):
        lines[start] = ALERT_PREFIX + base + suffix

open(cr_path, "w").write("\n".join(lines) + "\n")

# --- 2. Unwrap the CR's spec into a plain Prometheus rule file ---------------------------------
text = "\n".join(lines)
spec = text[text.index("spec:") + len("spec:"):]
header = (
    "# Code generated from " + cr_path + " by scripts/slo-render.sh. DO NOT EDIT.\n"
    "#\n"
    "# The same rules as the PrometheusRule CR, in the flat format `promtool check rules` and\n"
    "# `promtool test rules` require. Committed so both commands run against a real file with no\n"
    "# preprocessing; regenerated and diffed in CI, so it cannot drift from the CR or the spec.\n"
)
body = "\n".join(l[2:] if l.startswith("  ") else l for l in spec.splitlines()).lstrip("\n")
open(rules_path, "w").write(header + body + "\n")
PY

echo "OK: rendered ${CR} and ${RULES} from ${SPEC}."
