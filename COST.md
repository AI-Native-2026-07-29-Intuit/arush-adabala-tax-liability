# taxcalc-api — Cost Governance Runbook

W6 D4. What this service spends, which plane each charge lands on, which guardrail is supposed
to catch it, and what to do when one fires.

The single most important thing in this document: **there are two spending planes, and only one
of them is visible to AWS billing.** Every design decision below follows from that.

---

## Two cost planes

| Plane | What bills | Governed by | Attributed by |
|---|---|---|---|
| **AWS-resident** | NAT Gateway, RDS, S3, data transfer | `taxcalc-cost-dev` — a tag-scoped **AWS Budget** + a **CloudWatch billing alarm** | cost-allocation tags, read in Cost Explorer |
| **LLM (Anthropic)** | `claude-haiku-4-5` tokens for `explain-liability` | the **Anthropic Console workspace spend limit** | `CostLogger`'s per-request EMF line + the `X-Cost-Usd` header |
| **Embeddings (self-hosted)** | nothing per call — cluster CPU | pod `resources.limits` | not applicable |

**AWS Budgets cannot see Anthropic spend.** It is not AWS spend; it bills to the Anthropic
workspace and never appears in Cost Explorer, an `EstimatedCharges` alarm, or a Budget — not
because anything is misconfigured, but because AWS is not the merchant. An AWS Budget created to
cap LLM spend would deploy cleanly, report healthy, and control nothing. That is why the LLM
plane carries its own cap (at the platform) and its own attribution (in the application).

The third row is worth keeping in view because it is the counterexample: the embeddings service
is an "AI feature" with no per-request cost at all. It runs `bge-large-en-v1.5` on the cluster's
own CPU — no API key, no third party, and no NAT Gateway crossing, since the call resolves to a
cluster-internal Service DNS name. Its cost question is capacity planning, not per-call
accounting. "Is it AI?" is the wrong axis; "who is the merchant?" is the right one.

---

## Cost-allocation tags

Four mandatory keys on every billable resource:

| Key | Values | Notes |
|---|---|---|
| `service` | `taxcalc` | half of the Budget's `CostFilters` |
| `env` | `dev` \| `staging` \| `prod` | the other half |
| `tenant` | a tenant id, or `shared` | shared infrastructure is honestly labelled `shared` |
| `feature` | `egress`, `persistence`, `cost-governance`, `embeddings` | the resource's **own** purpose |

Applied to: `NatGateway*` and their EIPs (`cfn/taxcalc-network-dev.yaml`), `DbInstance`
(`cfn/taxcalc-app-dev.yaml`), and the cost stack's own SNS topic.

**Activation is manual and does NOT backfill.** Cost-allocation tag keys must be activated by
hand in the Billing console before they can be used as a filter, and spend incurred before
activation is never attributed, whatever the resource was tagged with at the time. Tagging a
resource is therefore only half the job; the console step is the other half, and it is the half
with no diff to review.

Three rules that are not stylistic:

- **Lowercase, and additive.** AWS tag keys are case-sensitive, so `Env` and `env` are two
  distinct cost-allocation keys requiring two separate activations. The four keys were **added
  alongside** the pre-existing `Env`/`Project` tags rather than renaming them; a rename would
  have been the tidier diff and would have silently orphaned every historical `Env`-keyed report.
- **`feature` names the resource's own purpose, not its loudest consumer.** The NAT Gateway is
  `feature=egress`, not `feature=explain-liability`, even though the Anthropic call crosses it.
  Tagging shared capacity after one consumer would attribute all egress to that feature and make
  the per-feature view actively misleading. The `feature` key earns its keep on the LLM plane,
  where `CostLogger` dimensions each call by the feature that actually made it.
- **Tag the cheap things.** The NAT Gateways' Elastic IPs are tagged, which the brief does not
  ask for. An attached EIP is free; a **detached** one bills ~$3.60/mo, and a detached EIP is
  precisely what a half-finished teardown leaves behind. Untagged, that charge is invisible to
  the tag-scoped Budget and would surface only on the account-wide alarm — the backstop, not the
  attribution.

A resource missing **either** `service` or `env` is invisible to the Budget. That does not merely
lose detail: it silently shrinks what the guardrail guards, while the guardrail keeps reporting
green. `scripts/cfn-guardrails.sh --static` check 5 asserts full coverage on every billable
resource type so this is a red CI check rather than a convention.

---

## The AWS-resident guardrails

### `taxcalc-monthly-cost-dev` — the tag-scoped Budget

`BudgetType: COST`, `TimeUnit: MONTHLY`, `BudgetLimit: $100`, filtered to
`user:service$taxcalc` + `user:env$dev`. Two notifications to SNS:

- **`FORECASTED > 80%`** — the only one of the two that can still change the outcome.
- **`ACTUAL > 100%`** — a post-mortem trigger.

**$100 is derived, not round.** Steady state is roughly: one dev NAT Gateway ~$32/mo before data
processing, `db.t4g.micro` + 20GB gp3 ~$15/mo, S3 in cents — call it ~$50. A limit at roughly 2x
steady state means `FORECASTED > 80%` ($80) fires because something changed, not because the
month has a 31st day in it. A limit set at $55 would page somebody every month for nothing, and
an alarm that cries wolf monthly is an alarm that gets muted.

### `taxcalc/estimated-charges-dev` — the account-wide billing alarm

`AWS/Billing` / `EstimatedCharges`, `Currency: USD`, threshold **$120**, `Period: 21600` (6h),
`EvaluationPeriods: 1`.

It is **account-wide and cannot be tag-filtered** — `EstimatedCharges` is one number for the
whole account. That is exactly why it complements the Budget rather than duplicating it: it
catches the spend the Budget is blind to by construction, i.e. anything untagged, or anything
somebody else created. The threshold sits **above** the Budget's limit deliberately, so the
tag-scoped Budget speaks first and this only fires once total account spend has passed the point
where the tagged view is no longer the whole story.

**`TreatMissingData: ignore`, and never `notBreaching`.** Billing refreshes roughly every 6h, so
missing datapoints are routine here. `notBreaching` counts a gap as OK, which on a metric with
routine gaps actively *erases* a real breach — the alarm goes ALARM, the next window has no data,
and it resets itself to OK. The opposite reasoning applies to a metric-pipeline alarm (an app
error rate), where silence means the pipeline is broken and `breaching` is the safe reading. Same
setting, inverted answer, because the two metrics fail in opposite directions.
`cfn-guardrails.sh --static` check 6 greps for the wrong spelling.

**The alarm is gated on a `us-east-1` Condition.** `EstimatedCharges` is published only to
us-east-1, for every region's spend. The same template deployed elsewhere would otherwise create
an alarm watching a metric that never receives a datapoint — sitting in `INSUFFICIENT_DATA`
forever, deployed, green, and unable to fire. An alarm that exists and cannot fire is strictly
worse than no alarm, because it is the one a reviewer ticks off. Verified from both sides on the
emulator: 4 resources in us-east-1, 3 in eu-west-1, the alarm being the difference. Outside
us-east-1 the `BillingAlarmName` output says so in words rather than returning a blank.

### `taxcalc-cost-alarms-dev` — the SNS topic

SSE at rest with `alias/aws/sns`. Its `TopicPolicy` allows `sns:Publish` from **both**
`budgets.amazonaws.com` and `cloudwatch.amazonaws.com`.

**Without that policy both guardrails silently fail to notify.** The Budget still shows as
configured and the alarm still transitions to ALARM; only the delivery is missing — the same
"green but dead" shape this whole stack exists to prevent, reproduced inside the stack itself.
Both statements carry `AWS:SourceAccount` conditions (not in the reference snippet): each service
principal is a confused-deputy candidate without one.

Subscriptions are deliberately **not** in the template. A subscription a human confirmed by email
does not belong in something that gets torn down and rebuilt.

---

## The NAT cost lever

`cfn/taxcalc-network-dev.yaml` gates NAT Gateway count on the `IsProdLike` condition:

| Env | NAT Gateways | ~Monthly | Trade |
|---|---|---|---|
| `dev` | 1 (AZ A) | ~$32 | losing AZ A costs dev its egress — acceptable |
| `staging` / `prod` | 3 (one per AZ) | ~$96 | an AZ failure takes out only its own subnet |

This is the single largest line item in the account and the one most worth understanding before
it appears on a bill. It is charged per gateway-hour **plus** ~$0.045/GB processed, so the fixed
cost is only half the story: every byte leaving the VPC crosses it. The Anthropic API call does;
the embeddings call does not, because it resolves to a cluster-internal Service.

To find it in Cost Explorer: group by the `service` tag, then look for usage type
`*-NatGateway-Hours` and `*-NatGateway-Bytes` under EC2-Other.

---

## The LLM plane

### What it costs

`explain-liability` calls `claude-haiku-4-5` — roughly a third of Sonnet's per-token price, and
the right tool for the job: a bounded liability record in, a short paragraph out, so Sonnet's
extra capability has nothing to act on. The model is named at the call site
(`LiabilityExplanationService.MODEL`) rather than taken from the application default, precisely
so that choice is visible in the diff of the feature that made it.

A measured live call (`AnthropicCostPathLiveIT`, real API, real tokens):

```
model=claude-haiku-4-5  resolved=claude-haiku-4-5-20251001
in=12  out=4  X-Cost-Usd=0.00005
```

### How it is attributed

`CostMiddleware` wraps every call and emits one CloudWatch **Embedded Metric Format** line per
request — namespace `uptimecrew/llmproxy`, dimensions `[[service, tenant, feature]]`, metrics
`CostUsd` and `LatencyMs`. Wherever stdout ships to CloudWatch Logs, that line is simultaneously a
log record and a real metric, with no metric-publishing call and no extra IAM permission.
Somewhere that does not ship stdout to CloudWatch, it is still a queryable JSON log line — it
degrades to something useful rather than to nothing.

**This is the LLM plane's entire cost attribution.** The per-feature dollar figure exists only
because it is written there; nothing in AWS can produce it.

Cost is carried as an integer count of **1e-5 USD**, which departs from `CLAUDE.md`'s scale-2
`BigDecimal` money rule — deliberately, and documented at `CostMiddleware`. That rule is right for
tax liability, where scale 2 *is* the domain. Per-call LLM cost sits four orders of magnitude
below it, so at scale 2 every call rounds to `0.00` and a million of them still round to zero.
The rule's actual intent — never accumulate money in binary floating point — is kept harder than
the letter would: `BigDecimal` at scale 8, rounded exactly once with HALF_UP, then carried as
integers, which sum without error.

### Two model ids, and why the log carries both

A request for `claude-haiku-4-5` comes back reporting `claude-haiku-4-5-20251001`. The requested
id is a floating **alias**; the response names the dated **snapshot** that served it. Measured
against the live API, not inferred.

- **Price by the alias** — `PriceBook` is keyed on it. Pricing off the response id throws
  `no price for model claude-haiku-4-5-20251001` on the first real call, and no stub-based test
  would ever catch it, because a stub echoes back whatever it was handed.
- **Log the snapshot** — an alias floats. A cost line recording only the alias cannot be
  reconciled against an invoice after the alias moves to a differently-priced snapshot.

### The guardrails, and which one does what

| Control | Where | What it actually does |
|---|---|---|
| Anthropic Console **workspace spend limit** | the platform | the hard cap. Enforced by the party doing the billing; survives this app being scaled to N replicas |
| `RateLimitFilter` (10 req/min/subject on `/summary`, `/explanation`) | the app | bounds the worst case **before** the money is spent — a retry storm is the failure most likely to produce a surprising invoice |
| `CostLogger` + `X-Cost-Usd` | the app | attribution only. Records; does not prevent |

There is deliberately **no in-app kill switch and no Redis counter.** That would be per-replica
state that under-counts by a factor of the replica count, plus a new failure mode (the cost store
being down) on the request path of a feature meant to degrade gracefully. The cap belongs at the
platform; the app's job is to say where the money went.

### `PriceBook` is the highest-maintenance file here

A stale price book makes every cost figure wrong while every test still passes: the arithmetic is
correct, the log line is well-formed, the header is present, and the number simply is not what the
invoice will say. Nothing in the process can detect it. Re-check it against Anthropic's published
pricing whenever a model is added or a rate changes.

It also uses one **blended** rate per model covering input and output together, while real
pricing charges output several times more than input. That is an approximation, taken knowingly:
it keeps the table auditable at a glance and is accurate in aggregate for a workload whose
input:output ratio is stable, which `explain-liability`'s is. It would be the wrong simplification
for a huge-prompt/one-word-answer workload, which would need input and output rates split.

---

## Runbooks

**Budget breach — `FORECASTED > 80%`.** Open Cost Explorer, group by the `service` tag, compare
against the previous month. The usual culprit is NAT Gateway data processing (a chatty new
integration, or a workload that started pulling images through the gateway) or an RDS instance
resized and not resized back. Check `*-NatGateway-Bytes` first.

**Budget breach — `ACTUAL > 100%`.** The money is spent. Same investigation, plus: decide whether
the limit is now wrong. A budget crossed three months running is a budget that needs re-deriving,
not a monthly alarm to acknowledge.

**Billing alarm (`EstimatedCharges`) fires but the Budget did not.** By construction this means
**untagged spend** — the alarm sees the whole account, the Budget sees only tagged resources.
Cross-reference: `aws resourcegroupstaggingapi get-resources --tag-filters Key=service,Values=taxcalc`
lists what *is* tagged; anything in Cost Explorer that is not in that list is the gap. Then fix
the tags at the template, not in the console — a console tag is drift.

**Neither fires but the bill is high.** Check that cost-allocation tags are still *activated* in
the Billing console (activation is account state, not template state, so a template change cannot
restore it), and that the SNS `TopicPolicy` still exists — a Budget with no publish permission is
silent, not healthy.

**LLM spend looks wrong.** Query the EMF metric `CostUsd` in namespace `uptimecrew/llmproxy`,
grouped by `feature`, and compare against the Anthropic Console. If the app's figure is *lower*
than the invoice, suspect `PriceBook` staleness or a model swap first — both under-report while
looking healthy. If the cost series went quiet, check for malformed EMF (CloudWatch drops the
metric and keeps the text) before concluding nothing spent money.

**API-key rotation.** Create the new key in the Anthropic Console, update the Kubernetes Secret
(`kubectl -n taxcalc-dev create secret generic anthropic-api --from-literal=ANTHROPIC_API_KEY=… --dry-run=client -o yaml | kubectl apply -f -`),
roll the Deployment, confirm `/explanation` still returns a non-zero `X-Cost-Usd`, then revoke
the old key in the Console. Revoke last, not first — the old key must stay valid until every pod
has the new one. The key is never committed: it lives in the Secret and in the operator's shell,
and `git grep` for the literal returns zero.

---

## What was actually verified, and on which engine

No AWS account is wired to either repository — `aws sts get-caller-identity` returns
`NoCredentials` and `vars.AWS_ACCOUNT_ID` is unset, unchanged since W6 D3. Everything below ran
against **floci 2.0.1**, the same local emulator W5 D4, W6 D1 and W6 D3 used, at
`AWS_ENDPOINT_URL=http://localhost:4566`. Endpoint only; no parameter or command changed.

**What floci genuinely established.** The whole stack reaches `CREATE_COMPLETE` through the real
`create-change-set → describe-change-set → execute-change-set` flow. The CloudWatch alarm is real
and readable:

```
$ aws cloudwatch describe-alarms --alarm-names "taxcalc/estimated-charges-dev"
taxcalc/estimated-charges-dev  AWS/Billing  EstimatedCharges  120.0  GreaterThanThreshold
```

And the `IsUsEast1` condition was verified **from both sides** — the same template produces 4
resources in us-east-1 and 3 in eu-west-1, the alarm being exactly the difference. That is the
kind of claim an emulator can settle, because it is about template evaluation rather than about
a service.

### Three parity gaps, each measured rather than assumed

**1. `AWS::Budgets::Budget` reports `CREATE_COMPLETE` against a service that does not exist.**

```
$ aws cloudformation describe-stack-resources --stack-name taxcalc-cost-dev
MonthlyCostBudget  AWS::Budgets::Budget  MonthlyCostBudget-7636191f  CREATE_COMPLETE

$ aws budgets describe-budget --account-id 000000000000 --budget-name taxcalc-monthly-cost-dev
An error occurred (UnknownOperationException): Unknown operation:
AWSBudgetServiceGateway.DescribeBudget
```

floci runs no `budgets` service at all — it is absent from `/_localstack/health`'s 100 services —
yet its CloudFormation provider accepts the resource, mints a plausible physical id, and reports
success. This is W6 D3's `bucket-policy-80a48155` finding in a new costume, and it lands on the
**headline resource of this deliverable**: the one guardrail that is supposed to enforce the
budget is the one the emulator cannot model. `CREATE_COMPLETE` here means the template parsed,
not that a budget exists.

**2. The SNS `TopicPolicy`, `KmsMasterKeyId` and tags are all silently dropped.** All three report
`CREATE_COMPLETE`; none reaches the SNS API. The live topic still carries the *default* policy:

```
$ aws sns get-topic-attributes --topic-arn …taxcalc-cost-alarms-dev  → Policy.Id
__default_policy_ID          # not AllowBudgetsPublish / AllowCloudWatchPublish
                             # Principal {"AWS":"*"} — WIDER than the template asks for
$ …  → Attributes.KmsMasterKeyId   → None
$ aws sns list-tags-for-resource   → (empty)
```

Worth stating plainly: on this endpoint the topic is **more permissive** than the committed
template, not less. An engineer who verified their least-privilege topic policy here would have
verified nothing.

**3. `TreatMissingData` is dropped from the alarm.** The alarm exists with the right namespace,
metric, threshold and action — and `describe-alarms` reports `TreatMissingData: None`. The single
property that decides whether this alarm fires correctly on a gappy metric is the one that did not
survive. That is a subtler failure than an outright rejection: the resource looks correct in every
field a reviewer would skim.

### Cost Explorer: not emulable, rather than unimplemented

The task asks for a Cost Explorer drill-down grouped by the `service` tag, the NAT line item
identified, and the view saved as a report. **None of that has a local path, and the reason is
worth stating precisely.** floci does run a `ce` service, and it answers the API correctly:

```
$ aws ce get-cost-and-usage --granularity MONTHLY --metrics UnblendedCost \
    --group-by Type=DIMENSION,Key=SERVICE
2026-08-01  total 0.0000000000  | groups: 36
2026-09-01  total 0.0000000000  | groups: 36

$ aws ce get-tags   →  {"Tags": [], "TotalSize": 0}
```

Thirty-six service groups, every amount exactly zero, and no cost-allocation tag keys known at
all. This is not a missing feature that a later floci version might add: **an emulator does not
bill anybody**, so there is no spend for Cost Explorer to report and no activated tag for it to
group by. Unlike the three gaps above — which are provider bugs and could be fixed — this one is
structural. The NAT Gateway line item can only be read on a real account.

The substitute is honest but weaker, and is not the same claim: `cfn-guardrails.sh --static`
check 5 asserts from the templates that every billable resource carries all four keys, so the
*input* to a tag-scoped report is verified even though the report itself is not.

Likewise `aws resourcegroupstaggingapi get-resources --tag-filters Key=service,Values=taxcalc`
returns an empty list here — partly the gap above, and partly because
`cfn/taxcalc-network-dev.yaml` does not currently deploy on this floci at all: it rolls back with
`A security group rule must specify exactly one of CidrIp, CidrIpv6, a prefix list, or a security
group` on `TaxcalcAppSecurityGroup`. **That is a pre-existing W6 D3 issue, not a W6 D4
regression** — confirmed by an A/B: the template as it stood *before* this deliverable's tag edits
fails identically, and the W6 D4 diff to that file is tag lines only.

**Summary of what each Done-When is worth:** the alarm and the stack creation are genuinely
verified; the Budget's existence, the topic policy and the tag-scoped Cost Explorer view are not,
and are labelled that way rather than counted as passes.

---

## `cost-author` audit

`.claude/skills/cost-author/SKILL.md` — authored locally, because `/cost-author` was **absent
from this session's skill listing**, the fourth deliverable running into that gap after
`cfn-author` (W6 D3), `argocd-author` (W6 D2) and `github-actions-author` (W6 D1).

**Provenance caveat, and it is not a formality.** A generator written by the same author as the
artefacts under review will tend to agree with them, and this pass was **not cold** — the cost
stack, the tag taxonomy and the cost package were all written before the skill was. A clean diff
here is therefore evidence of very little, and is reported as such.

- **Accepted:** *tag the Elastic IPs, not just the NAT Gateways.* The brief asks only for
  `NatGateway*` and the `DBInstance`. An attached EIP is free, so tagging it looks like busywork —
  until a teardown leaves one detached at ~$3.60/mo, untagged, and therefore invisible to the very
  Budget meant to catch it. Adopted in `taxcalc-network-dev.yaml`; it closes a hole the tag-scoped
  design creates by construction.

- **Rejected:** *raise the billing alarm threshold to track the Budget limit automatically.* The
  suggestion was to derive `BillingAlarmUsd` from `MonthlyBudgetUsd` (e.g. `1.2x`) so the two
  never drift. Declined: they are measuring **different things** — the Budget is tag-scoped and
  the alarm is account-wide — so coupling them encodes an assumption that this service is the only
  thing in the account. The day a second service lands, the account-wide threshold should move and
  the tag-scoped one should not. Two independent numbers with stated derivations beat one number
  with a multiplier.

The skill's own named failure modes were checked against the committed artefacts and recorded as
**not observed** rather than manufactured — no untagged billable resource, no `notBreaching`, no
AWS Budget aimed at Anthropic spend, no missing `TopicPolicy`, no mixed tag-key casing. They are
in the skill's non-negotiables, so a generator following it would not emit them; their absence is
weak evidence and is reported that way.

---

## Deploying the cost stack

```bash
aws cloudformation create-change-set --stack-name taxcalc-cost-dev \
  --change-set-name initial --change-set-type CREATE \
  --template-body file://cfn/taxcalc-cost-dev.yaml \
  --parameters ParameterKey=EnvName,ParameterValue=dev --region us-east-1
aws cloudformation describe-change-set --stack-name taxcalc-cost-dev --change-set-name initial
aws cloudformation execute-change-set --stack-name taxcalc-cost-dev --change-set-name initial
```

**us-east-1 is load-bearing, not a default** — see the alarm's Condition above.

Then activate the four tag keys in **Billing → Cost allocation tags**. Nothing in the template can
do this, and until it is done the Budget's filters match nothing.
