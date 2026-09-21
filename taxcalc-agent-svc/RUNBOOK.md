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

**What the cap can and cannot stop — read this before the first real firing.** The agent calls
`api.anthropic.com` **directly**, so no IAM policy can block a call in flight; AWS controls the
*key*, not the traffic. `DenyLlmSpendPolicy` therefore denies
`secretsmanager:GetSecretValue` on the Anthropic secret, which means External Secrets Operator
cannot refresh it and no restarted or newly-scheduled pod obtains one. **A pod already running
with the key in memory keeps spending** — that is what the per-request `BudgetGuard` and
`recursion_limit` are for. If you need spend to stop *now*, scale the Deployment to zero or
rotate the key; the BudgetAction alone will not do it.

`scripts/verify-budget-stack.sh` checks the template against AWS's published schemas, asserts the
action is configured to fire (100% / ACTUAL / AUTOMATIC / APPLY_IAM_POLICY), and evaluates the
policy's decisions offline — each with a negative control. What it cannot check is AWS's own
behaviour, so the first time this alarm is real, **verify the policy actually attached** rather
than assuming it did.

#### Stack verification record — 2026-09-20, floci 2.0.1 emulator

The done-when command, run against the **floci emulator** (`AWS_ENDPOINT_URL=http://localhost:4566`)
rather than an AWS account, via `FLOCI=1 ./scripts/verify-budget-stack.sh`:

```
$ aws cloudformation describe-stacks --stack-name taxcalc-agent-anthropic-monthly \
    --query 'Stacks[0].[StackName,StackStatus]' --output text
taxcalc-agent-anthropic-monthly	CREATE_COMPLETE

$ aws cloudformation describe-stack-resources --stack-name taxcalc-agent-anthropic-monthly \
    --query 'StackResources[].[LogicalResourceId,ResourceType,ResourceStatus]' --output text
DenyLlmSpendPolicy       AWS::IAM::ManagedPolicy        CREATE_COMPLETE
AgentSvcMonthlyBudget    AWS::Budgets::Budget           CREATE_COMPLETE
BudgetHardStop           AWS::Budgets::BudgetsAction    CREATE_COMPLETE
```

**Read the next three paragraphs before quoting that CREATE_COMPLETE anywhere.** It is an
emulator's answer, and this stack is the artefact whose emulator answer is least worth having.

*floci implements no Budgets service at all.* `aws budgets describe-budgets` returns
`UnknownOperationException: Unknown operation: AWSBudgetServiceGateway.DescribeBudgets`, so
`AWS::Budgets::*` is an opaque passthrough: the properties are stored and success is reported for
anything. Re-measured today — the same broken variant `cfn-lint` rejects (the four property names
called out in the `BudgetsAction` comment) deploys to **`CREATE_COMPLETE` on floci**. Step 5b of
the script is that negative control, and it runs on every `FLOCI=1` invocation so the claim stays
dated rather than inherited.

*And a new finding, sharper than the old one.* floci **does** implement IAM, so the DENY policy is
created for real and can be read back — and what comes back is:

```json
{"Sid": "DenyAnthropicKeyRead", "Effect": "Deny",
 "Action": ["secretsmanager:GetSecretValue"],
 "Resource": {"Ref": "AnthropicSecretArn"}}
```

The intrinsic was never resolved. Real CloudFormation substitutes the parameter value before IAM
sees the document; floci stored the template fragment verbatim and called the stack complete. On
AWS that policy is malformed — `Resource` takes an ARN string or a list of them, never an object.
So the emulator reported `CREATE_COMPLETE` for a managed policy AWS would have rejected: this
repository's recurring lesson in its fourth instance, **floci's most confident answer was its
wrongest**. The stack `Outputs` *did* resolve their `!Ref`s, which is exactly the kind of partial
fidelity that makes a green emulator run read as a verified one.

*One trap worth writing down*, because it produced a convincing false negative on the first
attempt: `ManagedPolicyName` is fixed at `DenyLlmProxyInvoke`, and floci's IAM **does** enforce
unique managed-policy names. Deploying the negative control while the good stack is still up
rolls it back on a name collision — which reads exactly like "floci rejected the broken template"
and is nothing of the kind. Step 5b deletes the good stack first and restores it afterwards for
that reason alone.

**What is therefore established about this stack:** it is well-formed YAML whose parameters,
`!Ref`s, `DependsOn` and `Outputs` resolve, whose resource lifecycle completes, and whose
properties are valid against **AWS's own published resource provider schemas** (step 1,
`cfn-lint`, proven to be a real gate by step 2's negative control) — plus an offline reproduction
of IAM's decision procedure (step 3). What remains unestablished: that AWS accepts the stack, and
that the Budgets service fires the action at 100%. Only an account and a month of real spend
answers those.

**Act.** Decide whether the spend was legitimate before restoring service. If it was a runaway,
find it first — the per-request ceiling should have caught a single runaway request, so a monthly
breach with no per-request breach means *volume*, not one bad request. To restore:
```bash
# Confirm it actually attached before concluding the cap worked:
aws iam list-attached-role-policies --role-name taxcalc-agent-svc-role

aws iam detach-role-policy --role-name taxcalc-agent-svc-role \
  --policy-arn arn:aws:iam::<acct>:policy/DenyLlmProxyInvoke

# ESO will not re-sync the key until its next refresh interval; restart the deployment to pick
# it up rather than waiting, since pods that never had the key stay broken.
kubectl -n taxcalc-svc rollout restart deploy/taxcalc-agent-svc
```
Then raise `MonthlyBudgetUsd` in `cfn/agent-svc-budget.yaml` through a reviewed change — the
BudgetAction will re-attach on the next evaluation otherwise, and you will be doing this again in
an hour.

#### If the api node is failing every request, check the bearer first

`TAXCALC_AGENT_BEARER_JWT` is what the agent presents to the MCP server. Unset, the SSE transport
is refused with **401 before the session is established**, and `/readyz` reports `mcp: down`
forever with a configuration that otherwise looks complete:

```bash
kubectl -n taxcalc-svc logs deploy/taxcalc-agent-svc | grep mcp.open
# mcp.open.failed ... -> the token is missing, wrong, or expired
# mcp.open.ok      ... -> the transport is up; look further down
```

Docs-only questions keep working throughout, which is what makes this easy to miss: the service
looks healthy, answers most traffic, and silently cannot use a single tool.

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

### Rehearsal record — 2026-09-20, k3d lab cluster

Rehearsed end to end against a real Argo CD (the W6 D2 instance), with the Application differing
from [`argo-apps/taxcalc-agent-svc.yaml`](argo-apps/taxcalc-agent-svc.yaml) only in `repoURL`
(an in-cluster git daemon rather than the GitHub config repo) and `project`. Auto-sync, prune,
self-heal and `ApplyOutOfSyncOnly` are the committed values.

| step | commit | wall clock |
|---|---|---|
| baseline, pods on `v1` | `46e836d` | — |
| roll forward: CI-style tag bump `v1` → `v2` | `922b3e3` | **65 s** to pods on `v2` |
| **roll back: `git revert` of the bump** | `e6dd102` | **310 s** to pods on `v1` |

**Reverted SHA:** `922b3e3` · **rolled back to:** `v1`, via revert commit `e6dd102`
**Verification (pod labels carry the prior image):**

```
$ kubectl -n taxcalc-svc get pods -l app=taxcalc-agent-svc \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'
taxcalc-agent-svc-6578c7f79b-z8jt4	taxcalc-agent-svc:v1

$ kubectl -n argocd get app taxcalc-agent-svc
NAME                SYNC STATUS   HEALTH STATUS
taxcalc-agent-svc   Synced        Healthy
# synced revision: e6dd102aade24c26f2246ce3262814f96b6f7747
```

**The rollback took nearly five times as long as the roll-forward, and that asymmetry is the
finding.** Both are one commit and one image swap; the difference is entirely Argo CD's polling.
A bump lands quickly when it happens to arrive just before a poll; a revert pushed just *after*
one waits out the full `timeout.reconciliation` (180 s by default) before the repo-server even
notices the commit, plus rollout time. Measured here: 65 s versus 310 s.

**So do not rely on auto-sync during an incident.** Push the revert, then force the refresh
rather than waiting for it:

```bash
argocd app get taxcalc-agent-svc --hard-refresh    # skip the poll interval
argocd app sync taxcalc-agent-svc                  # and the sync interval
```

Five minutes of unnecessary outage is the difference between a rollback that feels like a tool
and one that feels like a hostage situation. The numbers above are the *unassisted* path, which
is what you get if you push and walk away.

**Both images in this rehearsal are the same build under two tags.** That is deliberate: the
claim being tested is the deploy mechanism — bump, reconcile, revert, verify — not an
application behaviour change, and using one build keeps the timings about Argo CD rather than
about container startup differences.

**Re-verified 2026-09-20**, on the same lab cluster, after today's trajectory and Dockerfile
changes:

```
$ kubectl -n argocd get application taxcalc-agent-svc \
    -o jsonpath='{.status.sync.status}{"\t"}{.status.health.status}{"\t"}{.status.sync.revision}'
Synced	Healthy	e6dd102aade24c26f2246ce3262814f96b6f7747

$ kubectl -n taxcalc-svc get pods -l app=taxcalc-agent-svc \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'
taxcalc-agent-svc-6578c7f79b-z8jt4	taxcalc-agent-svc:v1
```

Still parked on the rolled-back revision, which is the point: nothing has quietly rolled forward
since. Read through `kubectl` rather than `argocd app get` — the CLI could not complete a gRPC
handshake through a `kubectl port-forward` on this host, and the Application CR carries the same
two fields the CLI prints. Prefer `argocd app get` where the CLI works; it also shows per-resource
sync state, which the jsonpath above does not.

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
- **Rehearse the rollback on the PROD Argo CD instance.** The record above is a k3d lab cluster
  whose Application differs from the committed one only in `repoURL` and `project`; the 180 s
  reconciliation asymmetry it measured is worth re-checking where the config repo is really
  GitHub and the repo-server is really polling it.
- **Deploy the budget stack to a real account.** Everything verified so far is schema-level plus
  an emulator that implements no Budgets service — see the stack verification record above.
- **Move retrieval behind the network.** 1.2 GB of the 1.63 GB image is a machine-learning stack
  this service links but barely uses; calling the W7 D3 sidecar over HTTP would take the agent
  image to roughly the size of the FastAPI app it actually is. (It was 4.51 GB until
  `.dockerignore` learned to exclude `**/.venv` — the path dependencies are copied in wholesale,
  host virtualenvs and all.)

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
