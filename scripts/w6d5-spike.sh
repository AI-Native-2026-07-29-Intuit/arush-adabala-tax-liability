#!/usr/bin/env bash
# scripts/w6d5-spike.sh - produce synthetic taxpayers.events so KEDA sees consumer-group lag and
# scales taxcalc-api-worker 0 -> N -> 0 (W6 D5 Task 4, RUNNABLE on k3d).
#
# Everything here happens in-cluster through the dev Kafka broker. No cloud, no SQS, no spend.
#
# WHY THIS PRODUCES THROUGH `kubectl exec` RATHER THAN A PORT-FORWARD
#
# A Kafka client bootstraps against one address and is then TOLD where to send its produce
# requests, by `advertised.listeners`. The broker advertises
# kafka.taxcalc-dev.svc.cluster.local:9092, which only resolves inside the cluster - so a
# host-side producer connected through `kubectl port-forward` completes its bootstrap, is handed
# an address it cannot reach, and hangs until its delivery timeout. That failure names a timeout,
# not a DNS problem, which is why it is worth avoiding rather than debugging.
#
# WHAT TO WATCH, AND THE ONE THING THAT SURPRISED US
#
# At rest, on a topic that has never been written to by a group that has never committed, KEDA
# does NOT report zero lag. A partition with no committed offset is an INVALID offset to the
# kafka scaler, and its default `scaleToZeroOnInvalidOffset: false` deliberately holds the
# Deployment at one replica rather than scaling to zero - the reasoning being that scaling to
# zero there would mean nothing ever commits, and the group could never recover on its own.
#
# A freshly deployed worker therefore sat at 1 replica with ACTIVE=True against an empty topic,
# which reads exactly like "KEDA thinks there is work when there is none". No bug, and producing
# once fixed it permanently - which for a while made THIS SCRIPT the thing that got a new cluster
# over that line, i.e. a scaling story whose at-rest state depended on someone having run a load
# generator by hand.
#
# It no longer does. k8s/taxcalc-api/kafka-bootstrap.job.yaml in the config repo seeds the group's
# committed offset at the log-end offset during the Argo CD sync that creates it - the same
# position `auto-offset-reset: latest` would have picked, just written down where the scaler can
# subtract it. A fresh deploy now rests at READY=True / ACTIVE=False / 0-0 with nothing produced
# at all, and this script is back to being only what it claims to be: a load generator.
#
# TWO SIZES, AND BOTH ARE REAL RUNS
#
#   COUNT=50      The deliverable's own check. Lands on a scaled-to-zero Deployment, so the
#                 records sit as lag until KEDA starts a pod: 0 -> 5 within one 15s poll
#                 (ceil(50/10)), then drained, then back to 0 after cooldownPeriod.
#   COUNT=60000   The mid-flight spike. Sized from the measured drain rate so the backlog
#                 outlives scale-up latency; needs the staging described below.
set -euo pipefail

# A working kubectl is not a given here - see the file for the Rancher Desktop shim this catches.
# shellcheck source=lib/kube-preflight.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/kube-preflight.sh"

NS="${NS:-taxcalc-dev}"
COUNT="${COUNT:-60000}"
TOPIC="${TOPIC:-taxpayers.events}"
GROUP="${GROUP:-taxcalc-read-model-builder}"
# tenant "tenant-synth" so a downstream cost tally can subtract this run's events from real usage.
TENANT="${TENANT:-tenant-synth}"
WORKER="${WORKER:-taxcalc-api-worker}"
SCALEDOBJECT="${SCALEDOBJECT:-taxcalc-worker-scaledobject}"
# auto | always | never. See need_staging() - `auto` is correct for both the small
# scale-from-zero check and the large mid-flight spike, which is why it is the default.
STAGE="${STAGE:-auto}"

KAFKA_BIN=/opt/kafka/bin

# STAGING THE BACKLOG: pause KEDA at zero, produce, then release.
#
# Without this the demo does not demonstrate anything. Two worker replicas across 12 partitions
# drain 4000 small JSON records in a couple of seconds, while KEDA's pollingInterval is 15s - so
# the backlog is gone before the scaler ever samples it, lag reads 0 at every poll, and the
# worker never scales past the replica count it already had. Nothing is broken; the measurement
# simply never happens.
#
# `autoscaling.keda.sh/paused-replicas: "0"` holds the Deployment at zero and stops KEDA acting
# on the trigger. The records pile up with nothing consuming them, and removing the annotation
# lets KEDA sample a real backlog and scale 0 -> N for the first time. This is staging a
# measurement, not faking one: what happens after the annotation comes off is entirely KEDA's
# decision, made from real lag on a real broker.
#
# WHEN STAGING IS UNNECESSARY, AND WHY THAT MATTERS FOR A SMALL PRODUCE
#
# The race staging closes only exists when something is already consuming. A worker sitting at
# 0 replicas drains nothing, by definition - so every record produced stays as lag until KEDA
# itself decides to start a pod, and the pause annotation would only re-implement a state the
# Deployment is already in.
#
# That is what makes the deliverable's own ~50-record check work where a 50-record mid-flight
# spike cannot. Fifty records against an already-running replica are gone in milliseconds and no
# poll ever sees them; fifty records against a scaled-to-zero Deployment are fifty messages of
# lag that persist until a pod exists, which is precisely the 0 -> >=1 transition being asked
# for. The size of the produce is not what decides whether the measurement happens - whether
# anything is consuming while it lands is.
need_staging() {
  case "${STAGE}" in
    always) return 0 ;;
    never)  return 1 ;;
  esac
  local current
  current=$($KUBECTL -n "${NS}" get deploy "${WORKER}" -o jsonpath='{.status.replicas}' 2>/dev/null)
  # Empty means the status has no replicas field yet, which is the API's way of saying zero.
  [ -n "${current}" ] && [ "${current}" != "0" ]
}
pause_at_zero() {
  echo "==> pausing KEDA at 0 replicas so the backlog can actually accumulate"
  $KUBECTL -n "${NS}" annotate scaledobject "${SCALEDOBJECT}" \
    autoscaling.keda.sh/paused-replicas="0" --overwrite >/dev/null
  # Wait for the Deployment to actually reach 0 - annotating is asynchronous, and producing
  # while a replica is still draining reopens the same race this function exists to close.
  for _ in $(seq 1 30); do
    replicas=$($KUBECTL -n "${NS}" get deploy "${WORKER}" -o jsonpath='{.status.replicas}' 2>/dev/null)
    [ -z "${replicas}" ] || [ "${replicas}" = "0" ] && break
    sleep 2
  done
  echo "    worker replicas now: ${replicas:-0}"
}

release() {
  echo "==> releasing KEDA (removing paused-replicas)"
  $KUBECTL -n "${NS}" annotate scaledobject "${SCALEDOBJECT}" \
    autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true
}
# Release even if the produce step fails, so a bad run never leaves the worker pinned at zero
# with a topic quietly filling up behind it.
#
# INT/TERM/PIPE as well as EXIT, and PIPE is the one that actually bit: piping this script into
# `head` closes stdout early, the write to a closed pipe raises SIGPIPE, and bash terminates
# WITHOUT running an EXIT-only trap. The worker then stays pinned at zero with a full topic and
# no indication why - the ScaledObject reads PAUSED=True, which is correct and says nothing about
# the script that abandoned it.
trap release EXIT INT TERM PIPE

if need_staging; then
  pause_at_zero
else
  echo "==> worker already at 0 replicas; nothing is draining, so no staging needed"
fi

echo "==> producing ${COUNT} synthetic records to ${TOPIC} in ${NS} (tenant=${TENANT})"

# The payload shape must match TaxpayerUpdatedEvent, because the consumer deserializes with
# spring.json.value.default.type pointed at that record and a shape mismatch routes every message
# to the DLT after retries. A DLT'd message IS consumed and DOES commit, so lag still falls and
# KEDA still scales down - the scaling demo would look perfect while the read model stayed empty.
# Any field added to TaxpayerUpdatedEvent has to be added here too.
#
# COUNT HAS TO BE BIG ENOUGH TO OUTLIVE THE CONTROL LOOP
#
# Measured on this cluster: ONE worker replica drains 6000 of these records in under 20 seconds.
# KEDA polls every 15s and a JVM worker pod needs ~40s to boot and join the group, so by the time
# a second replica could exist the backlog is already gone. KEDA then correctly declines to add
# it - there is no work left to justify it - and the run peaks at 1 replica.
#
# That is the autoscaler being right, and it is also a demo that shows nothing. For a visible
# 0 -> N -> 0 the backlog has to survive longer than scale-up latency, which means tens of
# thousands of records, not thousands. The default below is sized from that measurement rather
# than picked: ~10x the drain rate per pod, so the queue is still deep when the second and third
# pods arrive.
#
# Generating each line in a shell loop cannot produce at that rate - the loop itself becomes the
# bottleneck at a few hundred lines/sec. One batch is built once and replayed, which moves the
# cost to the broker where it belongs.
#
# BATCH IS CAPPED BY COUNT, AND THAT IS A CORRECTNESS FIX RATHER THAN A TIDY-UP
#
# With BATCH fixed at 1000, `REPEATS = ceil(COUNT/BATCH)` is 1 for every COUNT from 1 to 1000 -
# so `COUNT=50` produced one full batch of a thousand records and reported fifty. A produce that
# silently multiplies its own input by twenty is worse than one that refuses small counts,
# because the number in the run log is the number nobody re-derives. `head -n` then makes the
# total exact for counts that are not a whole multiple of the batch size.
BATCH=$(( COUNT < 1000 ? COUNT : 1000 ))
REPEATS=$(( (COUNT + BATCH - 1) / BATCH ))
$KUBECTL -n "${NS}" exec -i deploy/kafka -- bash -c "
  for i in \$(seq 1 ${BATCH}); do
    echo \"{\\\"aggregateId\\\":\\\"tp-synth-\${i}\\\",\\\"displayName\\\":\\\"taxcalc.example.internal/synth-\${i}\\\",\\\"filingStatus\\\":\\\"SINGLE\\\",\\\"homeJurisdiction\\\":\\\"FEDERAL\\\",\\\"createdAt\\\":\\\"2026-09-11T00:00:00Z\\\",\\\"tenant\\\":\\\"${TENANT}\\\"}\"
  done > /tmp/w6d5-batch.json
  for r in \$(seq 1 ${REPEATS}); do cat /tmp/w6d5-batch.json; done | head -n ${COUNT} \
    | ${KAFKA_BIN}/kafka-console-producer.sh --bootstrap-server localhost:9092 --topic ${TOPIC} \
        --producer-property linger.ms=50 --producer-property batch.size=65536
"

echo "==> produced. Watch KEDA scale the worker:"
echo "     $KUBECTL -n ${NS} get scaledobject taxcalc-worker-scaledobject -w"
echo "     $KUBECTL -n ${NS} get deploy taxcalc-api-worker -w"
echo
echo "==> current consumer-group lag:"
$KUBECTL -n "${NS}" exec -i deploy/kafka -- \
  ${KAFKA_BIN}/kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --describe --group "${GROUP}" 2>/dev/null | head -15
