# taxcalc-api — SRE Capstone (Week 6 Day 5)

Production-readiness checklist for `taxcalc-api`. Threads the W6 D1–D4 substrate (CI/CD, GitOps,
CloudFormation, cost SLI) onto today's scaling, tracing and load-test gate. W7 uses this document
as the template every capstone service's SRE doc begins from.

The day splits into two layers, and the split is the honest part: some of this ran on the k3d
cluster and was measured, and some of it is AWS-native and was written and defended without being
deployed. Each half is labelled, and nothing below is claimed as verified unless it was.

---

## Layer 1 — runnable on the k3d cluster (verified live)

| Artefact | Purpose | Verified by |
|---|---|---|
| `k8s/taxcalc-api/kafka.yaml` | A real single-node KRaft broker. W5 D3 shipped only a DNS placeholder; KEDA cannot scale on a hostname. | `kubectl -n taxcalc-dev get deploy kafka` → `1/1`; `kafka-consumer-groups.sh --describe` returns 12 partitions |
| `k8s/taxcalc-api/kafka-bootstrap.job.yaml` | Wave-0 sync hook that creates the topic and seeds the group's committed offset, so a **fresh** deploy rests at `0/0` instead of the 1-replica invalid-offset state | Job `succeeded=1`; re-run takes the guard path (`already has committed offsets … nothing to seed`) |
| `k8s/taxcalc-api/taxcalc-worker.deployment.yaml` | The KEDA scale target: same image, `SPRING_PROFILES_ACTIVE=k8s,worker`, no HTTP server, `replicas: 0` | `kubectl get deploy taxcalc-api-worker` → scaled `0 → 7 → 0` during the spike |
| `k8s/taxcalc-api/taxcalc-worker-scaledobject.yaml` | KEDA on `taxpayers.events` consumer-group lag, `lagThreshold: "10"`, scale-to-zero | `kubectl get scaledobject` → `READY=True`, `ACTIVE=True` under lag |
| `k8s/taxcalc-api/taxcalc-api.hpa.yaml` + `k8s/taxcalc-api/prometheus-adapter-values.yaml` | SLO-derived HPA on `taxcalc_inflight_requests`, not CPU | `kubectl get hpa` → `28375m/6`, `SuccessfulRescale … New size: 10` |
| `k8s/taxcalc-api/taxcalc-api.pdb.yaml` | Voluntary-disruption floor, `minAvailable: 2` | `kubectl get pdb taxcalc-api-pdb` → `MIN AVAILABLE 2`, `ALLOWED DISRUPTIONS 0` |
| `loadtests/taxcalc-api-p99.js` + `.github/workflows/load.yml` | k6 gate pinned to the W5 D5 SLO; `X-Cost-Usd` read as a real number | 214,030 requests, all five thresholds green (below) |

> **Where these files live.** Paths are the W6 D5 brief's own —
> `k8s/taxcalc-api/taxcalc-worker.deployment.yaml` and
> `k8s/taxcalc-api/taxcalc-worker-scaledobject.yaml` — in the **config repo**, which is the
> repository Argo CD reads. `base/` was renamed to `k8s/taxcalc-api/` and the `NN-` ordering
> prefixes dropped, so every filename now matches the layout it is graded against.
>
> **Two reasons not to do that turned out to be wrong, and both were mine.** The prefixes were
> described here as "the apply order Kustomize and the Argo CD sync waves are built around". They
> were not: ordering comes from the explicit `sync-wave` patches in `kustomization.yaml` and from
> the `resources:` list, both of which name files rather than infer order from them, and nothing
> in either repo ever ran `kubectl apply -f base/` where filename order would have mattered. The
> rename was also said to require changing `path:` on three Applications; the Applications point
> at `overlays/<env>`, not at the base, so the only path edits were the four overlays'
> `../../base` → `../../k8s/taxcalc-api`.
>
> What the rename genuinely cost was documentation: ~50 `base/NN-…` references across both repos'
> READMEs, `GITOPS.md`, this file, the AppProject guardrail script and the `aws-authored/` pack.
> That is a real cost and it is why the sweep is mechanical and verified (`kubectl kustomize`
> renders clean for `base`'s replacement and all four overlays; no `base/…` path survives except
> two deliberate quotations of an external reference layout). This repository's `manifests/`
> directory is still the **pre-GitOps W5 D3 copy** and deliberately does not carry the W6 D5
> files.

### The k6 gate, measured

In-cluster k6 Job, 200 VUs, 12 minutes, against the `loadtest` profile:

```
█ THRESHOLDS
  checks                ✓ 'rate>0.99'      rate=99.90%
  cost_per_request_usd  ✓ 'p(95)<0.003'    p(95)=0.00062
  cost_samples          ✓ 'count>0'        count=10609
  http_req_duration     ✓ 'p(99)<500'      p(99)=39.48ms
  http_req_failed       ✓ 'rate<0.005'     rate=0.04%

http_reqs ....... 214030  297.14/s
```

The cost figure comes from 10,609 real `X-Cost-Usd` header reads, not from a default. That
distinction is the whole reason `cost_samples: ['count>0']` is a threshold: without it, a
deployment that stopped emitting the header would make every sample
`parseFloat(undefined || '0')` = 0, and `0 < 0.003` would pass forever while reporting a control
that no longer exists.

### The integration spike

`scripts/w6d5-spike.sh` produced 60,000 synthetic `taxpayers.events` records:

```
18:24:22  worker=1   active=False      # backlog staged, KEDA released
18:24:47  worker=4   active=True       # first poll after release
18:25:12  worker=7   active=True       # peak
18:29:50  worker=7   active=False      # topic drained; cooldownPeriod (300s) begins
18:30:15  worker=0   active=False      # scale-to-zero
```

`0 → 7 → 0`, driven entirely by real consumer-group lag on a real broker.

### Task 1's Done-When, run as written

The spike above answers "does this scale under real load". It does not answer the deliverable's
actual check, which is smaller and stricter: **~50 records**, scale to ≥1 **within one polling
interval**, drain back to 0 after cooldown. Run on **2026-09-11** with `COUNT=50`:

```
at rest   READY=True  ACTIVE=False   worker 0/0   all 12 partitions LAG 0

12:40:50  worker=0   active=False    # 50 records land on a scaled-to-zero Deployment
12:41:05  worker=4   active=True     # +15s: the first poll after the produce
12:41:21  worker=5   active=True     # ceil(50/10) = 5, the documented lagThreshold math
12:41:36  worker=5   active=False    # drained; cooldownPeriod (300s) begins
12:46:24  worker=0   active=False    # scale-to-zero, 288s later
```

Scale-up inside a single 15s interval, `ceil(50/10) = 5` exactly as `lagThreshold: "10"`
predicts, and back to zero one cooldown later.

**Why fifty records work here and could not work before.** The earlier finding — that a
50-record produce is invisible because one replica drains it faster than KEDA polls — is true
only *while something is already consuming*. At zero replicas nothing drains, so all fifty
records persist as lag until KEDA itself starts a pod, which is precisely the `0 → ≥1` transition
being asked for. What decides whether the measurement happens is not the size of the produce but
whether the Deployment was at zero when it landed. `scripts/w6d5-spike.sh` now infers that: it
stages the backlog (pause at zero, produce, release) only when the worker is already running, and
skips staging when it is at zero.

Two bugs surfaced from running the check as written rather than reasoning about it:

- **`COUNT=50` produced 1,000 records and reported 50.** `BATCH` was hard-coded to 1000 and
  `REPEATS = ceil(COUNT/BATCH)` is 1 for every count from 1 to 1000, so any small produce was
  silently multiplied. A produce that misreports its own volume by 20× is worse than one that
  rejects small counts, because the number in the run log is the number nobody re-derives.
- **The at-rest row above needed the seed Job to be true on a fresh cluster.** It was only true
  here because a previous spike had already forced the group to commit — see finding 2 below.

---

## Layer 2 — authored and defended, never applied (AWS-native)

Under `aws-authored/` in the config repo. Each file opens with a comment stating why it is
author-only. None was ever `kubectl apply`'d or deployed.

| Artefact | Why it cannot run here |
|---|---|
| `aws-authored/karpenter-nodepool.yaml` | Karpenter provisions EC2 instances. k3d's nodes are Docker containers; there is nothing to launch. |
| `aws-authored/cfn/taxcalc-observability-dev.yaml` | `AWS::XRay::SamplingRule` is an AWS resource. |
| `aws-authored/adot-collector.yaml` | Half-runnable: the Tempo exporter is the W5 D5 endpoint this cluster has; the `awsxray` exporter needs AWS. |
| `aws-authored/taxcalc-worker-scaledobject.sqs.yaml` | No SQS queue, no IRSA role, no pod-identity webhook. |

The three decisions worth defending:

**Karpenter's `limits` exist because of KEDA and the HPA, not because of Karpenter.** `maxReplicaCount:
20` and `maxReplicas: 20` mean a runaway — a poison-pill loop that never commits, an Adapter
returning a stale high value — asks for forty pods, and Karpenter will faithfully launch whatever
that needs, because unbounded provisioning is its job. `limits: {cpu: 200, memory: 400Gi}` is the
only thing in the chain that says no. `consolidateAfter: 600s` is in lockstep with the HPA's
`scaleDown.stabilizationWindowSeconds`: shorter, and Karpenter removes a node during the HPA's
anti-flap hold, the load returns, and the cluster pays a node launch on top of a pod start.

**`FixedRate: 0` is the X-Ray trap, and it fails only when it matters.** The reservoir is an
absolute floor — the first 10 matching requests each second, recorded regardless of rate — and
`FixedRate` samples 5% above it. Setting `FixedRate: 0` looks disciplined and holds up under load.
It fails in the quiet window, which is exactly when an error spike is most diagnosable: at 3
requests/second a percentage samples essentially nothing, and the first traces of an incident are
simply absent. You find out while looking for them.

**`identityOwner: operator` is the SQS variant's entire point.** KEDA needs
`sqs:GetQueueAttributes`. With `identityOwner: workload` that permission lands on the *worker
pod's* IRSA role — a permission the application code never uses, carried by every replica,
inherited by anyone who reaches any worker pod, and still there long after the reason is
forgotten. With `operator` it lives on the KEDA operator's role, in the `keda` namespace, where a
reviewer asking "who can read this queue" finds the autoscaler. The principle generalises: a
credential belongs to the thing that makes the call, not the thing the call is about.
`taxcalc-worker-scaledobject.yaml` needs no `TriggerAuthentication` because the dev broker is PLAINTEXT — that is the
discipline not applying, not the discipline being skipped.

---

## What today actually taught, and none of it was in the plan

**1. Lag is a property of a consumer group, not of a Deployment.** The api pods run the same image
as the worker, so the moment a real broker existed they began consuming
`taxcalc-read-model-builder` — draining the very lag KEDA scales the worker on. Two or three api
replicas keep a dev-rate topic at zero lag however much is produced, so the worker never leaves
`minReplicaCount: 0`. Nothing about that looks like a failure: the ScaledObject is `READY=True`,
the trigger is valid, the broker is reachable, and the read model *is* being updated — by the
wrong pods. It reads as "KEDA isn't working" and sends you to operator logs that are clean. Fixed
by gating the listener's `autoStartup`; `TaxcalcWorker` refuses to start if the flag is ever false
in the worker run mode, because the inverse mistake produces a pod that joins the group, gets
partitions, reports Ready and never commits — so lag never falls and KEDA scales to
`maxReplicaCount` and holds there.

**2. An empty topic is not "no lag" to KEDA — it is an *invalid offset*.** A group that has never
committed has no offset to subtract, and the kafka scaler's default
`scaleToZeroOnInvalidOffset: false` deliberately holds the Deployment at one replica rather than
zero, on the reasoning that scaling to zero would mean nothing ever commits and the group could
never recover. So a freshly deployed worker sat at 1 replica with an empty topic, which reads
exactly like "KEDA thinks there is work when there is none".

Producing once resolves it permanently, and for a while that was the whole answer — which made
the documented at-rest state of the system contingent on somebody having run a load generator by
hand. `k8s/taxcalc-api/kafka-bootstrap.job.yaml` now seeds the group at the log-end offset during the
sync that creates it, so a fresh deploy rests at `READY=True` / `ACTIVE=False` / `0/0` with
nothing produced at all.

**The one-line fix here is a trap, and it is worth saying why.** Setting
`scaleToZeroOnInvalidOffset: "true"` on the trigger removes the symptom and creates a deadlock:
with an invalid offset *and* permission to scale to zero, the Deployment goes to zero, no member
ever joins, no offset is ever committed, and the offset stays invalid forever — the worker never
starts no matter what is produced. That flag's default is guarding against exactly what flipping
it would cause. Seeding a real committed offset is the fix that addresses the cause; the seeded
value is `--to-latest`, which is the same position the application's own `auto-offset-reset:
latest` would have chosen, so nothing is skipped or replayed. And because the hook re-runs on
every sync for the rest of the service's life, the `--list` guard is load-bearing rather than
tidy: `--reset-offsets --execute` *refuses* on a group with active members, so an unconditional
reset would fail the sync every time the worker happened to be scaled up.

**3. The consumer was faster than the control loop, so the first spike proved nothing.** One
replica drained 6,000 records in under 20 seconds while KEDA polls every 15s and a JVM pod needs
~40s to start. The backlog was gone before a second replica could be justified, KEDA correctly
declined to add one, and the run peaked at 1. That is the autoscaler being right and the
measurement never happening. The spike script now stages the backlog — pause at zero, produce,
release — and 60,000 records is sized from the measured drain rate, not picked.

**4. The real scaling ceiling is the ResourceQuota, and the binding resource is one nobody wrote.**
The HPA scaled correctly:

```
Normal  SuccessfulRescale  horizontal-pod-autoscaler  New size: 10; reason: pods metric
                                                      taxcalc_inflight_requests above target
Warning FailedCreate       replicaset-controller      Error creating: pods "taxcalc-api-…" is
                                                      forbidden: exceeded quota: taxcalc-dev-quota,
                                                      requested: limits.cpu=500m, used: limits.cpu=8,
                                                      limited: limits.cpu=8
```

The Deployment sat at `2/10` and stayed there. `limits.cpu` is not declared by any container in
this repo — W5 D3 deliberately omitted it to avoid CFS throttling — it is the namespace
LimitRange's `default: {cpu: 500m}` being stamped on admission. So every pod silently consumes
500m of an 8-CPU quota, the namespace tops out around sixteen pods across *all* workloads, and
`maxReplicas: 20` is unreachable by a factor of five. The HPA reports success, the Deployment
reports `2/10` forever, and the only trace is a ReplicaSet event nobody is watching. **An
autoscaler's maximum is a request, not a guarantee; the quota is the answer.**

**5. `public-key-location` does not beat `issuer-uri`, despite the auto-configuration's shape.**
The three `JwtDecoder` configurations are each `@ConditionalOnMissingBean(JwtDecoder)` and
public-key is declared first, which reads like it wins. It does not — with both set, the
issuer-uri decoder is built and the mounted key is silently ignored. The symptom is a flat 401
with a bare `WWW-Authenticate: Bearer`, nothing in the log, and no startup error (the decoder is
lazy), while the key is mounted, the profile is active and the pod is healthy. Every visible
signal says the token is bad.

**6. One image, two run modes, and `@EnableWebSecurity` does not know about run modes.** The worker
crash-looped on `required a bean of type 'JwtDecoder' that could not be found`. Boot's
`OAuth2ResourceServerAutoConfiguration` is `@ConditionalOnWebApplication(SERVLET)` and correctly
supplies nothing to a `web-application-type=none` process; `@EnableWebSecurity` carries no such
condition and imports `WebSecurityConfiguration` anyway, which demands the filter chain that needs
the decoder. The message names `JwtDecoder`, never the run mode, and the api pods running the
identical image are healthy at the time.

---

## AI-tool review — the `loadtest-author` audit

Run on a scratch branch against `loadtests/taxcalc-api-p99.js`.

**Accepted — the `cost_samples` counter.** The scaffold had `cost_per_request_usd` reading
`X-Cost-Usd` and a `p(95)<0.003` threshold, with no guard on whether any sample was ever recorded.
`parseFloat(undefined || '0')` is 0, so a deployment that stopped emitting the header produces a
run where every cost sample is 0 and the threshold passes — permanently, and while being reported
as a cost control. Adding `cost_samples: ['count>0']` makes the absence of the signal a failure
rather than a pass. This is the suggestion that materially improved the gate.

**Rejected — silently renormalising the workload mix.** The scaffold's mix summed to 1.05 and the
tool's correction was to scale each weight by `1/1.05`. That turns a typo into a *different test
that still passes*, and it is specifically wrong here: the LLM weight of 0.05 is not an aesthetic
choice, it is a ceiling forced by `RateLimitFilter`'s 10 requests/minute per JWT subject. Each VU
issues ~120 requests/minute, so 0.05 is ~6/min per subject; renormalising to 0.0476 would have
been harmless by luck, and renormalising *upward* from a different typo would have pushed every VU
into 429s and blown `http_req_failed` through its threshold — reading as "the service fell over
under load" when it is the cost control working as designed. Replaced with `assertMixSumsToOne()`,
which fails at startup naming the actual sum.

The tool's other known quirks did not fire on this run but are worth recording: inventing SLO
numbers rather than reading them from `slo/taxcalc-api.sloth.yaml`, and dropping the
`cost_per_request_usd` threshold entirely when the cost header is not obviously present.

---

## The three W7 readiness checks this substrate runs

Every service shipped in W7 is expected to pass all three before it is called production-ready.

1. **SLO budget** — `k6 run loadtests/<service>-p99.js` green on `http_req_duration: p(99)<500`
   and `http_req_failed: rate<0.005`, run as a required status check on the pull request, with the
   thresholds copied from the service's SLO rather than chosen.
2. **Cost-per-request budget** — `cost_per_request_usd: p(95)<0.003` read from a real `X-Cost-Usd`
   header, with a `cost_samples: count>0` guard proving the signal exists. A cost gate that cannot
   fail is not a control.
3. **AI-tool audit** — every AI-scaffolded load test or manifest reviewed against the checklist,
   with one accepted and one rejected suggestion documented and reasoned in the pull request.

---

## Known gaps, stated rather than left to be found

- **`manifests/` in the application repo was deliberately not extended.** The W5 D3 manifest set is
  a duplicate of the config repo's `k8s/taxcalc-api/`, and GITOPS.md already records that the migration
  direction is to delete it and point `k8s-ci.yml` at the config repo. Adding today's objects to a
  copy that is on its way out would deepen a drift that is already documented.
- **Argo CD was not running on this cluster**, so today's manifests were applied with `kubectl`
  from the config repo's own overlay rather than synced. The GitOps loop itself was proved in W6
  D2; what is unproven today is specifically that these new objects sync cleanly through it.
- **One NodePool spanning Spot and On-Demand does not keep the api off Spot.** A Spot reclaim is an
  involuntary disruption and the PDB does not apply to it. Doing this properly needs a second,
  on-demand-only NodePool plus a nodeSelector or taint on the api Deployment.
- **The `awsxray` exporter should be dropped** unless the capstone's AWS footprint grows. The LLM
  call is ordinary outbound HTTPS that Tempo already captures; X-Ray's value is AWS-side segments
  on SQS and RDS, neither of which is on the request path today.
- **107 of 10,716 LLM requests (1%) returned 429** during the 12-minute run. The 0.05 mix weight
  leaves ~40% headroom against the 10/min per-subject limit on average, but `Math.random()`
  clusters, and a VU that draws the LLM branch several times in quick succession exceeds it. The
  run still passed every threshold; a burst-tolerant weight would be ~0.03.
