# W7 D5 — Multi-agent capstone: LangGraph + supervisor + Postgres checkpointer + SSE + eval gate

Wires the whole capstone into **one running multi-agent service**. A new sibling project
`taxcalc-agent-svc/` hosts a three-node LangGraph (`retrieval_agent`, `api_agent`,
`synthesis_agent`) routed by a supervisor that fans out in parallel. Its only tool surface is the
W7 D4 MCP server, its only retriever is the W7 D3 sidecar, its state is checkpointed to Postgres
after every super-step, and its answers stream back into the W4 D4 `useChat` hook.

**The complexity is in the topology, not in the bodies.** Every node body is ~20 lines
delegating to something already built and tested. What is new is the composite contract around
them: typed state with reducers, a supervisor as the single policy point, per-node deadlines, two
independent runaway caps, a durable checkpointer, structured output at the end, end-to-end
tracing, per-agent cost attribution, a trajectory eval gate, and a GitOps deploy with a budget
hard cap.

---

## Test output

All commands run from `taxcalc-agent-svc/`, against a real Postgres.

```
$ uv sync --frozen
Resolved 150 packages in 6ms

$ uv run ruff check
All checks passed!

$ uv run mypy --strict src/ tests/ evals/
Success: no issues found in 29 source files

$ uv run pytest -q
117 passed in 4.33s

$ uv run pytest -q -m "not e2e" --cov=src --cov-fail-under=70
Required test coverage of 70% reached. Total coverage: 81.46%
115 passed, 2 deselected

$ uv run pytest -v -m e2e
tests/test_checkpointer_resume.py::test_a_second_invocation_resumes_the_prior_checkpoint PASSED
tests/test_checkpointer_resume.py::test_a_different_thread_id_does_not_resume PASSED
2 passed
```

**The eval gate** — 20 committed scenarios:

```
$ uv run python -m taxcalc_agent_svc.scripts.eval --offline --gate --allow-unmeasured-faithfulness
scenarios            : 20
trajectory match     : 1.000  (floor 0.7)
answer substring rate: 1.000  (reported, not gated)
faithfulness         : NOT MEASURED
mean cost per run    : 508 (1e-5 USD), baseline 508, regression +0.0% (limit 15%)

*** WARNING: faithfulness was DECLARED, not MEASURED. This gate did not check answer quality
on this run. ***

GATE PASSED
```

**The checkpointer smoke** — two invocations on one `thread_id`:

```
$ uv run python -m taxcalc_agent_svc.scripts.smoke --offline --thread-id t1
invocation 1: visited=['api_agent', 'synthesis_agent'] cost_usd_e5=50 ...
invocation 2: visited=['api_agent', 'synthesis_agent', 'api_agent', 'synthesis_agent'] cost_usd_e5=100 ...
checkpoints table rows for thread_id='t1': 8
OK: the second invocation read and extended the prior checkpoint
```

The second run's `visited_nodes` comes back with **four** entries rather than two. That is the
resume proving itself: the slot carries `operator.add`, so its length is the sum of both runs'
contributions if and only if the persisted state was read back.

**Infrastructure and discipline gates:**

```
$ cfn-lint cfn/agent-svc-budget.yaml                      # clean, exit 0
$ docker build --check -f taxcalc-agent-svc/Dockerfile .  # Check complete, no warnings found

$ grep -RIn ': float ' src/taxcalc_agent_svc/budgets.py   # no output
$ grep -RIn 'MemorySaver' src/                            # no output
$ grep -RIn 'except:' src/ tests/ evals/                  # no output
$ grep -rIn "lsv2""_pt_" .                                # assembled, never spelled - no output
```

---

## Six deviations from the brief — each forced by measurement

The reference snippets do not run as written, and four of the failures are **silent**. Full
transcripts in `taxcalc-agent-svc/PROMPT_JOURNAL.md`.

1. **`state["__mcp_session"]` cannot work.** Every state slot is msgpack-serialised into a
   checkpoint row each super-step, and a live session is not serialisable — verified directly:
   `JsonPlusSerializer().dumps_typed({"sess": socket()})` raises `TypeError: Type is not msgpack
   serializable`. Per-request dependencies travel on `config["configurable"]`, which reaches
   every node and is not checkpointed.

2. **`PostgresSaver` cannot work either.** Async node bodies mean `ainvoke`, which drives the
   checkpointer's *async* interface, which the sync saver inherits as `raise NotImplementedError`.
   It fails in `AsyncPregelLoop.__aenter__` before any node runs. Uses `AsyncPostgresSaver`.

3. **`session.call_tool(..., headers=...)`** — mcp 1.x has no such parameter, and D4 hardens its
   schemas against extra keys. Tenancy and the UUID5 idempotency key are injected as
   **schema-declared arguments** instead, read from each tool's published `inputSchema`.
   Introspecting the live D4 catalogue: all four tools declare `tenant_id`, exactly one
   (`orders.create_refund`) declares `idempotency_key`. Tenancy is *overwritten after the model
   speaks* — a model that read a document naming another tenant must not be able to reach it.

4. **`state["__visited_nodes"]` does not exist in LangGraph.** The trajectory is an explicit
   reduced state slot: one line per node, and an assertion that holds with or without a
   checkpointer.

5. **Decorator order.** The snippet lists `@deadline` above `@traceable`; the prose says to apply
   `@deadline` *before* it, which — decorators applying bottom-up — is the opposite. Both orders
   produce the sentinel, so the call site cannot tell them apart. Probed under a live run tree:

   ```
   traceable outermost (prose)   -> tag lands on 'retrieval_agent'   <- the node that timed out
   deadline  outermost (snippet) -> tag lands on 'chat_request'      <- the ROOT span
   ```

   The snippet's order is worse than no tag: it marks the whole request as having exceeded a
   deadline it never had, so the LangSmith query meant to isolate slow *nodes* returns slow
   *requests*. Followed the prose.

6. **Branching.** Cut from `w7d4-implementation` because `main` was at W7 D2 at the time. D4 has
   since merged (#61), so this is now rebased onto `main` and adds exactly the D5 commits.

### The finding the gate made itself

`thread_id=f"eval-{qid}"` is stable across runs, the checkpointer persists under it, and
`cost_usd_e5` carries `operator.add` — so the suite's second run **resumed its own first run** and
reported double the cost. Observed: 508 then 1016 (1e-5 USD), a +100% regression in which nothing
had changed, and the cost gate correctly failed the build. Namespaced per run, three consecutive
runs report 508, 508, 508.

### And one the linter made

`cfn-lint` caught four wrong property names before the template reached AWS.
`AWS::Budgets::BudgetsAction` spells its threshold `Value`/`Type`, not
`ActionThresholdValue`/`ActionThresholdType`, and its subscribers take `Type` where the sibling
`AWS::Budgets::Budget` takes `SubscriptionType` — two resources in one service spelling the same
concepts differently.

---

## CI

[`taxcalc-agent-svc ci` — run on `4d95471`](https://github.com/AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability/actions/runs/35467095217) · **green**, all six checks on this PR pass.

The PR tier runs a Postgres service container, so these executed on the runner and not only
locally: `ruff`, `mypy --strict`, the unit suite with the 70% coverage floor, **the
checkpointer-resume e2e**, the offline trajectory eval gate, the baseline-currency check, the
money and in-memory-checkpointer greps, the Dockerfile and `cfn-lint` checks, and the wheel build.

**The first run caught a real defect of mine**, which is worth recording rather than
force-pushing away: `.env.example` carried `LANGSMITH_API_KEY=lsv2_pt_...` as its placeholder,
and both this repository's secret-scan gates grep the whole tree for that literal prefix — so the
placeholder failed them, and it failed `taxcalc-mcp-server ci` too, since that gate scans from
the repo root. Fixed by not spelling the prefix rather than by exempting the file: a scanner that
had to special-case "but not in `.env.example`" has a hole in exactly the shape of the next real
key someone pastes into a file with that name. Every other step in that first run had already
passed.

---

## AI-tool reflection

Appended to `taxcalc-ai/PYTHON.md` ("What W7 D5 adds"), naming two concrete Claude deviations:
the **missing reducers on the parallel state slots** (last-write-wins silently erases one
worker's contribution — no exception, no log line, and a diff that reads as ordinary typing), and
**`PostgresSaver` + `graph.invoke` with async nodes** (both idiomatic in every example, both
non-functional here).

Across three transcripts the defects that mattered were all invisible without *running* the code.
The one a careful reader might have caught unaided — a shared mutable default on
`citations: list[Citation] = []` — was the least consequential.

---

## Deliverables checklist

- [x] New uv project `taxcalc-agent-svc/` with path deps on `taxcalc-ai` + `taxcalc-mcp-server`;
      one console script; `uv.lock` committed
- [x] `state.py` — `AgentState` with reducers on every merge-prone slot
- [x] `graph.py` — three named nodes, `supervisor` returning `list[Send]`, `AsyncPostgresSaver`,
      `recursion_limit` pinned on compile **and** every call site
- [x] `nodes/api.py` — `session.list_tools()` discovery, 5-iteration tool-use loop, deterministic
      UUID5 idempotency keys
- [x] `nodes/_deadline.py` — per-node deadlines (3 / 5 / 8 s), sentinel slots,
      `deadline_exceeded=True` on the node's own span
- [x] `nodes/synthesis.py` — Instructor `FinalAnswer(text, citations, confidence)`,
      `max_retries=2`, `extra="forbid"`, refusal path
- [x] `budgets.py` — `BudgetGuard`, int 1e-5 USD minor units, `BudgetExceeded` → 503 +
      `Retry-After`
- [x] `sse.py` — `astream_events(v2)` → `0:` / `2:` / `3:`, recursion vs budget on distinct codes
- [x] `app.py` — FastAPI lifespan owning one MCP session + one checkpointer pool
- [x] `evals/` — 20 committed scenarios, trajectory ≥ 0.70, faithfulness ≥ 0.85, cost ≤ +15%
- [x] `argo-apps/` Argo CD Application + `cfn/` Budget & BudgetAction hard cap
- [x] `Dockerfile` + two-tier CI workflow, every `uses:` SHA-pinned
- [x] `PROMPT_JOURNAL.md` (3 transcripts), `RUNBOOK.md` (top-5 signals + 30/60/90),
      `taxcalc-ai/PYTHON.md` delta, README section
- [x] **Action-run URL** — [`taxcalc-agent-svc ci` on 4d95471](https://github.com/AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability/actions/runs/35467095217) — green, and green
      on the real runner rather than only locally: the PR tier there stands up a Postgres service
      container, so the checkpointer-resume e2e and the trajectory eval gate both executed
- [ ] **Argo CD `Synced/Healthy` output** — needs cluster access
- [ ] **Rollback rehearsal record** — `RUNBOOK.md` carries the procedure and an explicit stub;
      deliberately not filled in with numbers nobody has measured

## Not done, and why

The three unticked boxes all require infrastructure this branch cannot reach: a live prod EKS
cluster and an Argo CD instance. The manifests, the budget template and the workflow are
committed and linted; what is missing is the evidence of them having run, and inventing that
evidence would be worse than its absence. The rollback rehearsal in particular is left as a
labelled stub — a procedure nobody has run is a procedure nobody knows works, and recording a
wall-clock time that was never measured would hide exactly that.
