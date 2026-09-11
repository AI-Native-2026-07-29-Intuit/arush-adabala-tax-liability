# COST.md has moved

The cost-governance runbook now lives at **`taxcalc-api/COST.md` in the config
repository**, beside `taxcalc-api/INFRA.md`:

<https://github.com/AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability-config/blob/main/taxcalc-api/COST.md>

## Why it is there and not here

Most of what the runbook governs is there. The tag taxonomy, the Budget, the
billing alarm, the SNS topic and the NAT cost lever are all CloudFormation in
`cfn/`, and the two static checks that enforce them are `cfn-guardrails.sh`
checks 5, 6 and 7. A runbook one repository away from every artefact it
describes, and from the gate that enforces it, is a runbook that goes stale
without anything going red.

W6 D3 set the precedent with `taxcalc-api/INFRA.md`, and the task text names
the path `taxcalc-api/COST.md` directly.

## What stayed in this repository

The LLM cost plane's implementation, which AWS billing cannot see and no
CloudFormation template can govern:

| | |
|---|---|
| per-request cost accounting | [CostLogger.java](src/main/java/com/uptimecrew/tax_liability/llm/cost/CostLogger.java) |
| the `X-Cost-Usd` response header | [CostResponseHeader.java](src/main/java/com/uptimecrew/tax_liability/llm/cost/CostResponseHeader.java) |
| the middleware that records the call | [CostMiddleware.java](src/main/java/com/uptimecrew/tax_liability/llm/cost/CostMiddleware.java) |
| wiring | [CostTrackingConfig.java](src/main/java/com/uptimecrew/tax_liability/config/CostTrackingConfig.java) |

The runbook explains what those emit and why the cap for that spend sits at the
Anthropic Console rather than in this application.
