---
name: cost-author
description: Scaffold a cost-governance layer for a service - a four-key cost-allocation tag taxonomy, a tag-scoped AWS Budget with FORECASTED/ACTUAL notifications, a CloudWatch billing alarm on AWS/Billing EstimatedCharges, the SNS topic and TopicPolicy both publish through, and the in-app per-request cost accounting for any LLM spend AWS billing cannot see. Use when asked to author, scaffold, or independently review cost governance for a service.
---

# cost-author

## Provenance — read this before trusting the output

This skill was **authored locally**, not distributed by the course. The W6 D4
deliverable names `/cost-author` as a provided tool; it was absent from the
session's skill listing, exactly as `cfn-author` was for W6 D3,
`argocd-author` for W6 D2 and `github-actions-author` for W6 D1. Four
deliverables, four times the same gap.

The consequence for the audit is the same as it was for `cfn-author`, and it
does not get weaker by repetition: **a generator written by the same person
who wrote the artefacts under review will tend to agree with them.**
Under-disagreement is the expected failure mode, so a clean diff is evidence
of very little. Any audit citing this skill must say so, and must say whether
the pass was genuinely cold.

## Arguments

```
/cost-author <repo-or-service> [--service <s>] [--env <e>] [--budget <usd>] [--out <dir>]
```

Arriving as one raw string; parse it yourself.

| Argument | Values | Default |
|---|---|---|
| `<repo-or-service>` | positional, required | — |
| `--service` | the `service` tag value | the positional arg |
| `--env` | `dev`, `staging`, `prod` | `dev` |
| `--budget` | monthly USD cap | `100` |
| `--out` | directory | `.cost-author-out/` |

## Hard rule: this is a cold pass

**Do not read the repository's existing `cfn/taxcalc-cost-dev.yaml`, its
`COST.md`, or the application's cost package before generating.** The entire
value of this skill is that its output was produced without sight of the
artefacts it will be compared against.

Derive the taxonomy and thresholds from the arguments and the reasoning below.
For figures that must be real — the NAT Gateway's monthly rate, the RDS
instance class — read the infrastructure templates' *parameters*, not their
cost stack. If a figure is genuinely unavailable, emit a clearly-marked
placeholder rather than guessing silently.

Write to `--out` or a `scratch/cost-author` branch. **Never overwrite tracked
artefacts.**

## The one idea this skill exists to enforce

**There are two spending planes, and only one of them is visible to AWS
billing.**

| Plane | What bills | Governed by |
|---|---|---|
| AWS-resident | NAT Gateway, RDS, S3, data transfer | a tag-scoped AWS Budget + a CloudWatch billing alarm |
| LLM / third-party | Anthropic (or OpenAI, or any hosted model) tokens | the provider's own platform spend cap + in-app per-request accounting |

Every recommendation below follows from that split. The most common and most
expensive mistake in this area is proposing an AWS Budget to control LLM
spend: **AWS Budgets cannot see spend that is not AWS spend.** Such a budget
deploys cleanly, reports healthy, and controls nothing. Never emit one, and
flag it as a finding if you are reviewing artefacts that contain one.

## Artefacts to generate

### 1. The tag taxonomy — exactly four keys

```
service   the billing service name, e.g. taxcalc          (constant per service)
env       dev | staging | prod                            (from --env)
tenant    the tenant a resource is attributable to, or `shared`
feature   the product feature a resource exists for       (e.g. egress, persistence)
```

Rules that are not negotiable:

- **Lowercase keys.** AWS cost-allocation tag keys are case-sensitive, so
  `Env` and `env` are two distinct keys requiring two separate activations.
  Pick one case and never mix.
- **Additive, never a rename.** If a resource already carries `Env`/`Project`
  tags, ADD the four keys alongside. Renaming detaches the resource from every
  historical report keyed on the old name.
- **Every billable resource carries all four.** A tag-scoped Budget filters on
  `service` AND `env`; a resource missing either key is invisible to it. That
  does not merely lose detail — it silently shrinks what the guardrail guards
  while the guardrail keeps reporting green.
- **`feature` names the resource's own purpose, not its loudest consumer.**
  Shared infrastructure (a NAT Gateway, a database) gets `egress` /
  `persistence`, never the name of one feature that happens to use it. Tagging
  shared capacity after a consumer makes the per-feature view actively
  misleading.
- **Tag the cheap things too.** An Elastic IP attached to a NAT Gateway is
  free; a *detached* one bills ~$3.60/mo, and a detached EIP is exactly what a
  half-finished teardown leaves behind. Untagged, that charge is invisible to
  the Budget.

Emit a note that **cost-allocation tags must be activated by hand in the
Billing console and do NOT backfill.** Spend incurred before activation is
never attributed, whatever the resource was tagged with at the time.

### 2. The tag-scoped Budget

`AWS::Budgets::Budget`, `BudgetType: COST`, `TimeUnit: MONTHLY`,
`CostFilters.TagKeyValue` on `user:service$<service>` and `user:env$<env>`.

Two notifications, both to SNS:

- `FORECASTED > 80%` — the only one of the two that can still change the
  outcome. Put it first.
- `ACTUAL > 100%` — a post-mortem trigger.

**Derive the limit, do not pick a round number.** Sum the known monthly rates
(one dev NAT Gateway ~$32 before data processing; a db.t4g.micro + 20GB gp3
~$15; S3 in cents), then leave genuine headroom. A budget set at roughly 2x
steady state means `FORECASTED > 80%` fires because something changed, not
because the month has 31 days in it. State the arithmetic in a comment.

`Fn::Sub` note: `user:env$${Env}` needs the doubled `$` to emit a literal
separator before interpolation. A single `$` makes CloudFormation look for a
variable named `{Env}` and silently produces a filter matching every
environment at once.

### 3. The account-wide billing alarm

`AWS::CloudWatch::Alarm` on `AWS/Billing` / `EstimatedCharges`,
`Dimensions: [{Name: Currency, Value: USD}]`.

- **`TreatMissingData: ignore`, never `notBreaching`.** Billing publishes
  roughly every 6h, so gaps are routine; `notBreaching` reads each gap as OK
  and resets a real breach. (A metric-pipeline alarm wants the opposite
  answer — silence there means the pipeline broke. Same setting, inverted
  reasoning, because the two metrics fail in opposite directions.) Reject
  `notBreaching` on any alarm whose metric has routine gaps.
- **`Period: 21600`** (6h), matching the metric. Shorter buys nothing and
  widens the window with no datapoint.
- **us-east-1 only.** `EstimatedCharges` is published solely to us-east-1, for
  every region's spend. Gate the alarm on the region with a `Conditions` entry
  rather than letting the same template create an alarm elsewhere that sits in
  `INSUFFICIENT_DATA` forever — deployed, green, and unable to fire. An alarm
  that exists and cannot fire is worse than no alarm, because it is the one a
  reviewer ticks off.
- **Set the threshold ABOVE the Budget's limit.** The tag-scoped Budget should
  speak first; this is the backstop for untagged spend it cannot see.
- **`OKActions` as well as `AlarmActions`**, so recovery is as visible as the
  breach.

### 4. The SNS topic and its policy

`AWS::SNS::Topic` with `KmsMasterKeyId: alias/aws/sns`, plus an
`AWS::SNS::TopicPolicy` allowing `sns:Publish` to **both**
`budgets.amazonaws.com` and `cloudwatch.amazonaws.com`.

**Without that policy, Budgets and CloudWatch silently fail to publish.** The
budget still shows as configured and the alarm still transitions to ALARM; only
the delivery is missing. Emit `AWS:SourceAccount` conditions on both statements
— each service principal is a confused-deputy candidate otherwise.

Do not put subscriptions in the template. A subscription a human confirmed by
email does not belong in something that gets torn down.

### 5. The LLM cost plane (in-app, not CloudFormation)

Where the service calls a hosted model, emit the accounting the AWS plane
cannot provide:

- **Cost from the provider's own usage block**, never estimated from the
  prompt. An estimate cannot see the tokens the model actually generated, and
  a figure that drifts from the invoice is worse than none because it gets
  trusted.
- **Integer minor units.** Compute in `BigDecimal`, round once, then carry an
  integer count of 1e-5 USD. Per-call costs sit far below the usual scale-2
  money convention — at scale 2 every call rounds to `0.00` and a million of
  them still round to zero. Integers also sum without error.
- **A structured cost log per call**, in CloudWatch Embedded Metric Format:
  namespace per service, dimensions `[[service, tenant, feature]]`. Build the
  JSON with a serialiser, not string concatenation — `tenant` is
  caller-influenced, and CloudWatch answers malformed EMF by dropping the
  metric and keeping the text, so the cost series quietly reads low.
- **An `X-Cost-Usd` response header**, formatted with `BigDecimal`, not
  `Double.toString` (which switches to scientific notation below 1e-3 — a
  realistic per-call cost renders as `2.0E-4` and downstream parsers read it as
  near-zero).
- **A hard cap at the provider's platform**, not in the application. An in-app
  kill switch is per-replica state that under-counts by the replica count and
  adds a new failure mode on the request path.
- **Rate limiting on the paid routes.** It is the only one of the three
  controls that bounds spend *before* it happens.

### 6. Known failure modes to reject on review

When auditing rather than generating, these are findings:

| Finding | Why it matters |
|---|---|
| An untagged NAT Gateway, RDS instance or EIP | invisible to the tag-scoped Budget; the guardrail silently guards less |
| `TreatMissingData: notBreaching` on a gappy metric | erases real breaches; the alarm looks healthy |
| An AWS Budget proposed to cap LLM/Anthropic spend | AWS Budgets cannot see non-AWS spend; controls nothing |
| An SNS topic with no TopicPolicy | budget/alarm never actually notify |
| A billing alarm outside us-east-1 | the metric is never published there; permanent INSUFFICIENT_DATA |
| Mixed tag-key casing (`Env` and `env`) | two separate cost-allocation keys, two activations, split reports |
| A price table for model costs with no staleness note | every cost figure silently wrong while all tests pass |
| Cost accumulated in `double` | rounding drift on a figure that is reconciled against an invoice |

## Output format

For each generated artefact, emit the file and a short rationale block. End
with an **Assumptions** section listing every figure you could not derive, and
a **Provenance** section restating the caveat at the top of this file.
