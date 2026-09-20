# taxcalc-agent-svc — on-call runbook

The five signals that should page someone, what each one means, what to do about it, and the
30/60/90 plan. Written for whoever is holding the pager at 02:00 and has never read this code.

**Probes.** `livenessProbe` -> `/healthz`, `readinessProbe` -> `/readyz`, and they are
deliberately different endpoints. `/healthz` reaches no dependency: a liveness probe that checked
downstreams would have Kubernetes *restart* a working pod whenever one blipped, hardest exactly
when the downstream is already struggling. `/readyz` is gated on the checkpointer alone - without
it no request of any shape can run - and *reports* the MCP session without gating on it, because
a docs-only question routes `retrieval_agent -> synthesis_agent` and touches no tool. Taking the
pod out of service for traffic it can still answer is a self-inflicted outage.

**There is no deploy-ordering requirement.** The service starts and serves whether or not the MCP
server and Postgres are up yet; both are opened on first use, with backoff, and `/readyz` reports
the truth in the meantime. This was not true of the first version - see "What the deployment
rehearsal found" below.

**What this service is, in one paragraph.** A FastAPI process hosting a three-node LangGraph
(`retrieval_agent`, `api_agent`, `synthesis_agent`) routed by a supervisor that fans out in
parallel. Its only tool surface is the W7 D4 MCP server; its only retriever is the W7 D3 sidecar;
its state is checkpointed to Postgres after every super-step. It streams answers to the W4 D4
React app over the Vercel AI SDK data-stream protocol.

---

## Top five signals

### 1. `synthesis_cost_per_request` p99 breach

**What it means.** The synthesis node is spending more per request than it used to. The usual
cause is a context that has grown — a retrieval change that returns more or larger documents
feeds straight into the synthesis prompt.

**Confirm.** In LangSmith, filter the `taxcalc-agent-svc-dev` project to `synthesis_agent` runs
and compare prompt token counts against last week's. In CloudWatch, compare
`synthesis_cost_per_request` against `retrieval_cost_per_request` — if both moved, it is
retrieval; if only synthesis moved, it is the prompt.

**Act.** Not an outage on its own: `BudgetGuard` caps each request at
`TAXCALC_AGENT_COST_CEILING_USD_E5` and the monthly `BudgetAction` caps the account. If it is
climbing fast, lower `TAXCALC_AGENT_COST_CEILING_USD_E5` — requests that would have breached the
old ceiling return 503 with `Retry-After` rather than running. That is a deliberate
availability-for-cost trade; make it consciously.

### 2. `retrieval_cost_per_request` p99 over the node deadline

**What it means.** Retrieval is timing out. The deadline is 3 s
(`TAXCALC_AGENT_DEADLINE_RETRIEVAL_S`) and a miss lands `{"docs": []}` — so the service keeps
answering, from the api node's evidence alone, at lower confidence.

**Confirm.** LangSmith, `metadata.deadline_exceeded = true`, filtered to `retrieval_agent`. That
filter works because the deadline decorator is applied *inside* `@traceable` — if the query
returns `chat_request` runs instead of node runs, the decorator order has regressed and
`tests/test_deadline.py` should have caught it.

**Act.** Check the sidecar's Postgres and Redis first — a cold cross-encoder or a slow pgvector
query is the usual cause. Degradation is graceful by design, so this is urgent-but-not-paging
unless confidence is dropping with it.

### 3. RAGAS faithfulness 7-day median drop > 0.10

**What it means.** Answers are drifting away from their cited context. Either retrieval is
returning worse documents or the synthesis prompt has changed.

**Confirm.** The production sampler scores `TAXCALC_AGENT_RAGAS_SAMPLE_RATE` of traces and writes
the score back into LangSmith run metadata. Compare the 7-day median against the trailing 30-day.
Then run the committed suite locally: `uv run python -m taxcalc_agent_svc.scripts.eval` (no
`--gate`, so it reports without failing).

**Act.** If the trajectory match is also down, it is a routing regression — look at
`supervisor()`'s keyword table first. If trajectory is fine and faithfulness is down, it is
retrieval quality or the prompt.

### 4. `BudgetAction` fired

**What it means.** Monthly Anthropic spend hit 100% of budget and AWS attached the DENY policy to
`taxcalc-agent-svc-role`. **The service is now failing every model call.** This is a designed
hard stop, not a malfunction.

**Confirm.**
```bash
aws budgets describe-budget-actions-for-budget --budget-name taxcalc-agent-anthropic-monthly
aws iam list-attached-role-policies --role-name taxcalc-agent-svc-role   # DenyLlmProxyInvoke present
```

**Act.** Decide whether the spend was legitimate before restoring service. If it was a runaway,
find it first — the per-request ceiling should have caught a single runaway request, so a monthly
breach with no per-request breach means *volume*, not one bad request. To restore:
```bash
aws iam detach-role-policy --role-name taxcalc-agent-svc-role \
  --policy-arn arn:aws:iam::<acct>:policy/DenyLlmProxyInvoke
```
Then raise `MonthlyBudgetUsd` in `cfn/agent-svc-budget.yaml` through a reviewed change — the
BudgetAction will re-attach on the next evaluation otherwise, and you will be doing this again in
an hour.

### 5. Argo CD `OutOfSync`

**What it means.** The cluster no longer matches the config repo. With `selfHeal: true` this
normally resolves itself within the sync window; an Application that *stays* OutOfSync means
reconciliation is failing, not drifting.

**Confirm.**
```bash
argocd app get taxcalc-agent-svc
argocd app diff taxcalc-agent-svc
```

**Act.** Read the diff before syncing. If someone hotfixed the cluster by hand, `selfHeal` has
already reverted it — the fix must go through git, which is the guardrail working. If the sync
itself is failing, the usual causes are a missing secret in the namespace or an image tag that
does not exist in ECR.

---

## Rollback

Argo CD reconciles from the config repo, so rollback is a git operation and not a redeploy.

```bash
# 1. In the config repo, revert the image tag bump.
git revert <the bump commit>            # note both SHAs: reverted, and rolled back to
git push

# 2. Watch Argo CD pick it up (auto-sync; ~3 min window).
argocd app wait taxcalc-agent-svc --health --timeout 300

# 3. Verify the pods actually carry the prior image.
kubectl -n taxcalc-svc get pods -l app=taxcalc-agent-svc \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'
```

**Roll forward rather than back when the change is a prompt or a threshold**: those are
config, and a revert takes the code with them.

> **Rehearsal record.** Fill this in the first time it is exercised against prod, in the PR that
> does it: reverted SHA, rolled-back-to SHA, wall-clock time for auto-sync to complete, and the
> `kubectl` output above showing the prior image. A rollback procedure nobody has run is a
> procedure nobody knows works — and the first attempt must not be during an incident.

---

## In-flight requests during a restart

They survive. State is checkpointed to Postgres after every super-step, so a request whose pod is
killed mid-graph resumes on the replacement when the client re-issues with the same `thread_id`.
This is proven, not assumed — `tests/test_checkpointer_resume.py` closes the first connection
pool entirely before building the second, so nothing but the database carries state across.

What does **not** survive is the SSE connection: the client sees the stream end and must
reconnect. The `thread_id` is what makes that reconnect a resume rather than a restart.

---

## What the deployment rehearsal found

Two defects, both found by trying to deploy this rather than by testing it, and both fixed on
this branch.

**1. The service could not start unless every dependency was already up.** The first lifespan
opened the MCP SSE session and the Postgres checkpointer eagerly and let either failure
propagate. Under Kubernetes that is a process that exits before it listens - `CrashLoopBackOff`,
with exponential backoff, so a dependency that was briefly unreachable kept the service down long
after it returned, and any rollout coinciding with an MCP blip failed outright. It also
contradicted this service's own `/healthz` reasoning, one layer up where no probe configuration
could soften it. Fixed in `runtime.py`: both dependencies are opened lazily behind a lock and a
retry, and readiness is gated on the checkpointer alone.

**2. The production image shipped ~4.7 GB of CUDA libraries to a CPU-only service.** `torch`
arrives transitively through the W7 D3 reranker, and PyPI's Linux wheel bundles the whole NVIDIA
CUDA runtime - which this service, running on CPU nodes, will never load. Measured: **9.19 GB**
built from the unpinned lock, against a 1.2 GB virtualenv on macOS where PyPI's wheel is already
CPU-only. Pinning the PyTorch CPU index for Linux removed all 43 `nvidia-*` packages and took the
image to **4.48 GB - a 51% reduction**. That is time off every pull, every rollout and every
image scan, and it is the difference between a node that can hold this image and one that evicts
it.

The remaining 4.48 GB is still dominated by `torch` itself plus `transformers`, `scipy` and
`pyarrow`. The real fix is for the agent to reach the reranker over the network - it already
reaches every *tool* that way through MCP - rather than linking a machine-learning stack into a
web service's image. Recorded as a documented trade-off rather than smuggled into this branch.

## 30 / 60 / 90

### Day 30 — production hardening

- **Interrupt-and-approve HITL.** Today's HITL is checkpoint-resume. The next step is
  `interrupt_before=["api_agent"]` so a human authorises `orders.create_refund` before it fires.
  The machinery is already here: the checkpointer can hold a paused state across the minutes or
  hours a human takes.
- **Replace keyword routing with a model router.** `supervisor()` is already the single policy
  point, so this is one function body, not a topology change.
- **Per-tenant rate limits and cost ceilings**, also in `supervisor()` — a tenant on a small plan
  should get a smaller `cost_usd_e5` ceiling, decided before any worker runs.
- **Rehearse the rollback** and fill in the record above.
- **Move retrieval behind the network.** 4.48 GB of the image is a machine-learning stack this
  service links but barely uses; calling the W7 D3 sidecar over HTTP would take the agent image
  to roughly the size of the FastAPI app it actually is.

### Day 60 — scope expansion

- **New MCP tools reach the agent with no change here.** The api node discovers its catalogue
  through `session.list_tools()` and injects tenancy from each tool's published schema, so a
  fifth tool is a D4 deploy and nothing else.
- **Grow the eval suite past twenty rows**, weighted toward the routing branches that page most.
- **Streaming synthesis.** The `0:` channel is wired and the bridge forwards
  `on_chat_model_stream` deltas; Instructor's structured output currently resolves in one shot,
  so the first visible token is the whole answer. Partial-object streaming closes that.

### Day 90 — scale

- **Multi-region.** The checkpointer is the stateful part; a regional Postgres with read replicas
  and thread-affinity routing is the shape.
- **Tenant-isolated connection pools**, so one tenant's slow corpus cannot exhaust the pool every
  other tenant's retrieval shares.
- **Trace-driven regression suites**: promote real production traces that scored badly into the
  committed scenario suite, so each incident permanently widens the gate.
