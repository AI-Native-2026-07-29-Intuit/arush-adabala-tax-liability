# taxcalc-agent-svc/evals/trajectory.py
"""Trajectory eval: twenty scenarios, each asserting a golden node sequence and an answer floor.

**Why a trajectory eval rather than only an answer eval.** An answer-quality score says whether
the final text was good; it says nothing about *how* the graph got there. A supervisor that
routed every question to both workers would produce perfectly good answers at twice the cost and
latency, and a faithfulness score alone would call that a healthy service. Conversely a routing
regression that sent a refund question to retrieval only would produce a confident, well-formed,
completely ungrounded answer - which scores well on fluency and is exactly the failure this file
exists to catch.

So each row asserts two independent things: the sequence of nodes that ran, and the quality of
what they produced. A regression in either fails the build.

**The match is an ordered subsequence - not equality, and not a bare subset.**
``trajectory_match`` returns 1.0 when the expected nodes appear among the visited ones in the
expected order, with unexpected nodes between them allowed. A graph that grows a fourth node - a
re-ranking pass, a guardrail - should not fail twenty scenarios for doing more work on the way to
the same answer; a graph that stops visiting an expected node, or that visits them in a different
order, should fail. See :func:`trajectory_match` for why the ordering is deterministic enough to
assert on.

**Three gates, not one, and the third is the one usually missing.** Trajectory match catches
routing regressions. Faithfulness catches answer regressions. Cost-per-run catches the change
that improves both by spending three times as much - a prompt that stuffs the whole corpus into
context scores *better* on faithfulness while quietly tripling the bill, and no quality metric
will ever object. That is why the cost comparison is a gate and not a dashboard.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Where a run's summary is written, and where the NEXT run reads its cost baseline from. In git
#: on purpose - a baseline that lived only in CI cache would vanish on eviction and silently stop
#: gating, which is the failure mode this repository has been bitten by before.
LAST_RUN_PATH = Path(__file__).parent / "last_run.json"

#: Gate floors. Named constants because the CI workflow's job is to RUN this gate, not to restate
#: its thresholds - a threshold written in YAML as well as here is a threshold that drifts.
TRAJECTORY_FLOOR = 0.70
FAITHFULNESS_FLOOR = 0.85
COST_REGRESSION_LIMIT = 0.15


@dataclass(frozen=True)
class Scenario:
    """One evaluated question.

    Frozen because a scenario is a fixture: a harness that could mutate the expectation it is
    checking against is a harness that can make itself pass.

    :ivar qid: Stable identifier, used as the checkpoint thread id so each scenario gets its own
        conversation and cannot inherit another's state.
    :ivar question: The question to ask.
    :ivar tenant_id: Whose data to answer from. Varied across the suite so a tenancy regression
        that leaked one tenant's corpus shows up as a faithfulness drop rather than passing
        because every row used the same tenant.
    :ivar expected_nodes: The golden node sequence, asserted as an ordered subsequence of what
        actually ran - see :func:`trajectory_match`.
    :ivar expected_answer_substring: A token the answer should contain. A weak signal on its own,
        which is why it is *reported* rather than gated - see :func:`run_eval`.
    """

    qid: str
    question: str
    tenant_id: str
    expected_nodes: tuple[str, ...]
    expected_answer_substring: str


#: The committed suite: twenty rows, in this file rather than in a data file beside it.
#:
#: Inline because the floors this module defines and the expectations they are applied to belong
#: in front of the same reader: one file answers both "what is asserted" and "at what threshold".
#: These rows lived in a sibling ``scenarios.jsonl`` until the deliverable's wording - a Scenario
#: dataclass and a list of 20 rows - was taken at its word, and the move paid for itself twice:
#: ``mypy --strict`` now type-checks the fixtures themselves rather than only the loader that
#: parsed them, and a malformed row is a syntax error at import rather than a ``KeyError`` twenty
#: minutes into a CI run.
#:
#: Three routing branches, deliberately weighted: docs-only (7), api-only (7), both (6). A suite
#: that exercised one branch twenty times would report a healthy trajectory score with two thirds
#: of the router unchecked. Tenants rotate a/b/c for the reason given on :attr:`Scenario.tenant_id`.
SCENARIOS: list[Scenario] = [
    # Docs-only: the default branch, and the one a routing regression degrades most
    # quietly - a question that stops reaching retrieval still gets a fluent answer.
    Scenario(
        qid="docs-only-01",
        question="what is the policy on taxpayer returns",
        tenant_id="tenant-a",
        expected_nodes=("retrieval_agent", "synthesis_agent"),
        expected_answer_substring="policy",
    ),
    Scenario(
        qid="docs-only-02",
        question="how do i claim a home office deduction",
        tenant_id="tenant-b",
        expected_nodes=("retrieval_agent", "synthesis_agent"),
        expected_answer_substring="deduction",
    ),
    Scenario(
        qid="docs-only-03",
        question="what is the standard deduction for a sole trader",
        tenant_id="tenant-c",
        expected_nodes=("retrieval_agent", "synthesis_agent"),
        expected_answer_substring="deduction",
    ),
    Scenario(
        qid="docs-only-04",
        question="which rule governs late filing penalties",
        tenant_id="tenant-a",
        expected_nodes=("retrieval_agent", "synthesis_agent"),
        expected_answer_substring="penalt",
    ),
    Scenario(
        qid="docs-only-05",
        question="where in the docs is the mileage allowance defined",
        tenant_id="tenant-b",
        expected_nodes=("retrieval_agent", "synthesis_agent"),
        expected_answer_substring="mileage",
    ),
    Scenario(
        qid="docs-only-06",
        question="how do i register for VAT",
        tenant_id="tenant-c",
        expected_nodes=("retrieval_agent", "synthesis_agent"),
        expected_answer_substring="register",
    ),
    Scenario(
        qid="docs-only-07",
        question="what is the policy on amended returns",
        tenant_id="tenant-a",
        expected_nodes=("retrieval_agent", "synthesis_agent"),
        expected_answer_substring="amend",
    ),

    # API-only: an order id or a tool verb, which must NOT drag retrieval along. This is
    # the branch that catches a supervisor fanning out to everything - perfect answers at
    # twice the cost, which every quality metric calls healthy.
    Scenario(
        qid="api-only-01",
        question="what is the status of order ord-synth-9001",
        tenant_id="tenant-b",
        expected_nodes=("api_agent", "synthesis_agent"),
        expected_answer_substring="ord-synth-9001",
    ),
    Scenario(
        qid="api-only-02",
        question="show me order ord-synth-9002",
        tenant_id="tenant-c",
        expected_nodes=("api_agent", "synthesis_agent"),
        expected_answer_substring="ord-synth-9002",
    ),
    Scenario(
        qid="api-only-03",
        question="refund ord-synth-9003 for 10.00",
        tenant_id="tenant-a",
        expected_nodes=("api_agent", "synthesis_agent"),
        expected_answer_substring="ord-synth-9003",
    ),
    Scenario(
        qid="api-only-04",
        question="has order ord-synth-9004 shipped",
        tenant_id="tenant-b",
        expected_nodes=("api_agent", "synthesis_agent"),
        expected_answer_substring="ord-synth-9004",
    ),
    Scenario(
        qid="api-only-05",
        question="what is the status of my latest filing",
        tenant_id="tenant-c",
        expected_nodes=("api_agent", "synthesis_agent"),
        expected_answer_substring="status",
    ),
    Scenario(
        qid="api-only-06",
        question="issue a refund of 25.00 on ord-synth-9005",
        tenant_id="tenant-a",
        expected_nodes=("api_agent", "synthesis_agent"),
        expected_answer_substring="ord-synth-9005",
    ),
    Scenario(
        qid="api-only-07",
        question="list the line items on order ord-synth-9006",
        tenant_id="tenant-b",
        expected_nodes=("api_agent", "synthesis_agent"),
        expected_answer_substring="ord-synth-9006",
    ),

    # Both: the parallel fan-out, and the only branch where the reducers are exercised.
    # `retrieval_agent` precedes `api_agent` because `supervisor()` appends its Sends in
    # that order - see trajectory_match on why that ordering is asserted rather than
    # ignored.
    Scenario(
        qid="both-01",
        question="look up order ord-synth-9001 and tell me the refund policy",
        tenant_id="tenant-c",
        expected_nodes=("retrieval_agent", "api_agent", "synthesis_agent"),
        expected_answer_substring="refund",
    ),
    Scenario(
        qid="both-02",
        question="what is the status of ord-synth-9007 and which rule covers it",
        tenant_id="tenant-a",
        expected_nodes=("retrieval_agent", "api_agent", "synthesis_agent"),
        expected_answer_substring="rule",
    ),
    Scenario(
        qid="both-03",
        question="how do i refund order ord-synth-9008",
        tenant_id="tenant-b",
        expected_nodes=("retrieval_agent", "api_agent", "synthesis_agent"),
        expected_answer_substring="refund",
    ),
    Scenario(
        qid="both-04",
        question="check order ord-synth-9009 against the deduction policy",
        tenant_id="tenant-c",
        expected_nodes=("retrieval_agent", "api_agent", "synthesis_agent"),
        expected_answer_substring="deduction",
    ),
    Scenario(
        qid="both-05",
        question="what is the refund policy and the status of ord-synth-9010",
        tenant_id="tenant-a",
        expected_nodes=("retrieval_agent", "api_agent", "synthesis_agent"),
        expected_answer_substring="refund",
    ),
    Scenario(
        qid="both-06",
        question="show the docs for refunds and the status of order ord-synth-9011",
        tenant_id="tenant-b",
        expected_nodes=("retrieval_agent", "api_agent", "synthesis_agent"),
        expected_answer_substring="refund",
    ),
]


def load_scenarios() -> list[Scenario]:
    """Return the committed scenario suite.

    A function rather than a bare module constant so callers cannot mutate the suite in place:
    :data:`SCENARIOS` holds frozen rows, but the list around them is not, and an eval that could
    ``.pop()`` a failing scenario off its own suite is an eval that can make itself pass.

    :returns: A fresh list of the committed scenarios, in file order.
    """
    return list(SCENARIOS)




def trajectory_match(actual: tuple[str, ...], expected: tuple[str, ...]) -> float:
    """Score one run's node sequence against its golden one.

    The expected nodes must appear in ``actual`` **in order**, with gaps allowed - a subsequence
    match, not a subset one and not equality. Each of those three is a different claim:

    * *Equality* would fail twenty scenarios the day the graph grows a fourth node - a re-ranking
      pass, a guardrail - for doing more work on the way to the same answer. That is how an eval
      suite becomes something people delete rather than fix.
    * *Subset* is what this function used to do, and it cannot see an ORDERING regression: a
      graph that ran synthesis before its evidence arrived, or that reversed the fan-out, scored
      a clean 1.0 while answering from nothing. The whole point of a trajectory eval is that it
      checks the path and not only the destination.
    * *Subsequence* keeps the tolerance for extra nodes and recovers the ordering assertion.

    Ordering is a fair thing to assert here because the order is deterministic, not incidental:
    :func:`taxcalc_agent_svc.graph.supervisor` appends its ``Send`` list in a fixed order, and
    LangGraph applies each task's writes at fan-in in that task order rather than in completion
    order. So a ``both-*`` row sees ``retrieval_agent`` before ``api_agent`` whichever worker
    finishes first, and a change to that order is a change to the routing policy - which is
    exactly what this gate exists to notice.

    :param actual: The nodes that ran, from the ``visited_nodes`` state slot.
    :param expected: The golden nodes.
    :returns: 1.0 when every expected node ran in the expected order, 0.0 otherwise. Binary
        rather than a fraction because a partial trajectory is not a partial success: a run that
        visited two of three expected nodes produced an answer missing a whole evidence source,
        and averaging that to 0.67 would let a suite of such runs clear a 0.70 floor.
    """
    # One shared iterator, consumed left to right: each expected node is searched for only in
    # what remains AFTER the previous one was found, which is the subsequence test in one line.
    remaining = iter(actual)
    return 1.0 if all(node in remaining for node in expected) else 0.0


def cost_regression(current_e5: int, baseline_e5: int) -> float:
    """Fractional change in cost per run against the committed baseline.

    :param current_e5: This run's mean cost per scenario, in 1e-5 USD minor units.
    :param baseline_e5: The previous run's, same units.
    :returns: The fractional change - 0.20 means 20% more expensive. Returns 0.0 against a zero
        or absent baseline, which is the honest answer for a first run: there is nothing to
        compare against, and reporting an infinite regression would fail a build for the crime
        of being the first one.
    """
    if baseline_e5 <= 0:
        return 0.0
    return (current_e5 - baseline_e5) / baseline_e5


def load_baseline(path: Path = LAST_RUN_PATH) -> int:
    """Read the committed cost-per-run baseline.

    :param path: The summary file from the previous committed run.
    :returns: The baseline mean cost in 1e-5 USD minor units, or 0 when absent.
    """
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text()).get("mean_cost_usd_e5", 0))
    except (ValueError, OSError):
        return 0


async def run_eval(
    graph: Any,
    settings: Any,
    scenarios: list[Scenario] | None = None,
    *,
    score_faithfulness: bool = True,
) -> dict[str, Any]:
    """Run every scenario and summarise the three gated metrics.

    Each scenario gets its own ``thread_id``, namespaced by a per-run id, so neither one row's
    state nor one *run's* state can leak into the next. See the inline comment on ``run_ns``: the
    naive choice of ``qid`` alone made the suite resume its own previous run and report a
    doubling cost regression on every invocation.

    :param graph: The compiled graph.
    :param settings: Validated configuration, for the run config.
    :param scenarios: The suite; defaults to the committed one.
    :param score_faithfulness: Run RAGAS. Disabled when there is no evaluator credential, in
        which case ``faithfulness`` is reported as ``None`` - see :func:`score_with_ragas` on why
        ``None`` rather than a default.
    :returns: The summary, also written to :data:`LAST_RUN_PATH`.
    """
    # Imported here rather than at module scope so this file can be imported - by a unit test of
    # `trajectory_match`, by the CI gate's `--help` - without constructing settings or a graph.
    from taxcalc_agent_svc.budgets import BudgetGuard
    from taxcalc_agent_svc.graph import run_config
    from taxcalc_agent_svc.runtime import Dependencies

    suite = scenarios if scenarios is not None else load_scenarios()

    # A FRESH thread namespace per eval run, and this is not a detail - it is the difference
    # between a working cost gate and one that fires on every run forever.
    #
    # The obvious thread id is the scenario's qid, which is stable and readable. It is also
    # wrong: the checkpointer persists state under that id, `cost_usd_e5` carries operator.add,
    # and so the second run of the suite RESUMES the first run's checkpoint and reports double
    # the cost. Measured, not predicted - the first two runs of this gate reported 508 then 1016
    # (1e-5 USD), a +100% "regression" caused entirely by the eval talking to itself, and the
    # cost gate correctly failed a build in which nothing had changed.
    #
    # The scenarios stay independent AND reproducible this way: each run is isolated from every
    # other, and within a run each scenario still gets its own thread, so one row cannot inherit
    # another's state. The checkpointer's resume behaviour is proven in
    # tests/test_checkpointer_resume.py, which is where that claim belongs.
    # A real MCP session provider, the same `Dependencies.session` the FastAPI app passes - and
    # LAZY, so an offline run that never reaches the api node opens no connection. This was
    # `session=None`, which made every tool-routed scenario - seven api-only plus six both, 13
    # of the 20 - fail with `AttributeError: 'NoneType' object has no attribute 'list_tools'`.
    # Offline mode could never show it, because offline mode stubs the api node out entirely:
    # the gate's own green run was the reason nobody looked.
    deps = Dependencies(settings)

    run_ns = uuid.uuid4().hex[:12]
    matched = 0
    answer_hits = 0
    total_cost_e5 = 0
    rows: list[dict[str, Any]] = []

    for sc in suite:
        guard = BudgetGuard(settings.cost_ceiling_usd_e5)
        thread_id = f"eval-{run_ns}-{sc.qid}"
        cfg = run_config(thread_id, settings, guard=guard, session=deps.session)
        # ainvoke, not invoke: every node body is async, and the synchronous entry point raises
        # `TypeError: No synchronous function provided` before running any of them.
        # One scenario's failure must not destroy the other nineteen. A provider error - a 401,
        # a rate limit, a spend cap - propagates out of `ainvoke` and, unguarded, aborts the whole
        # run with a traceback and NO summary: the trajectory half, which needed no model at all
        # and had already been measured for the rows that ran, is lost along with it. Measured
        # here: a workspace usage cap on row one produced a bare stack trace and zero metrics.
        #
        # So the row is recorded as a failure and the suite continues. That is not swallowing
        # the error - the row has no answer and no visited nodes, so it scores 0.0 on trajectory
        # and is unscorable for faithfulness, both of which fail the gate loudly and for the
        # right reason. The error text rides along in the summary so the reader learns WHY.
        error = ""
        try:
            state = await graph.ainvoke(
                {"question": sc.question, "tenant_id": sc.tenant_id, "thread_id": thread_id},
                config=cfg,
            )
        except Exception as exc:  # broad by design - see above; the row carries the reason
            error = f"{type(exc).__name__}: {exc}"
            state = {}
        visited = tuple(state.get("visited_nodes", ()))
        m = trajectory_match(visited, sc.expected_nodes)
        matched += int(m == 1.0)

        answer_text = _answer_text(state.get("answer"))
        hit = sc.expected_answer_substring.lower() in answer_text.lower()
        answer_hits += int(hit)

        cost = int(state.get("cost_usd_e5", 0))
        total_cost_e5 += cost
        rows.append(
            {
                "qid": sc.qid,
                "match": m,
                "visited": list(visited),
                "expected": list(sc.expected_nodes),
                "answer": answer_text,
                "answer_substring_hit": hit,
                "cost_usd_e5": cost,
                "contexts": hydrate_contexts(state.get("docs", [])),
                "question": sc.question,
                "error": error,
            }
        )

    # The judge gets the SAME credential the service uses, read from Settings, rather than
    # whatever `ANTHROPIC_API_KEY` happens to be in the environment. Those are not the same
    # thing here: this project's settings are prefixed (`TAXCALC_AGENT_ANTHROPIC_API_KEY`), and
    # langchain-anthropic reads the bare name - so a CI job that supplies only the prefixed
    # secret would run the whole suite and then report faithfulness as NOT MEASURED, failing the
    # build for a missing variable nobody knew it needed.
    try:
        await deps.aclose()
    except RuntimeError as exc:
        # `Attempted to exit cancel scope in a different task than it was entered in` - the MCP
        # session's transport was opened lazily inside a LangGraph NODE task, which has since
        # finished, and anyio refuses to unwind it from here. That is a defect in the runtime's
        # ownership of that stack, not in the suite, and it must not cost the run its results:
        # every scenario has already executed by this point, and letting teardown throw would
        # discard twenty rows of measurements over a socket that the process exit closes anyway.
        print(f"WARNING: MCP session teardown failed ({exc}); results below are unaffected")

    faithfulness = (
        score_with_ragas(rows, api_key=settings.anthropic_api_key.get_secret_value() or None)
        if score_faithfulness
        else None
    )
    baseline = load_baseline()
    # Integer division: the mean of integer minor units is reported in the same units, never
    # promoted to a binary fraction. See budgets.py on why money does not become a float here.
    mean_cost = total_cost_e5 // len(suite) if suite else 0

    summary = {
        "scenarios": len(suite),
        "trajectory_match": matched / len(suite) if suite else 0.0,
        "answer_substring_rate": answer_hits / len(suite) if suite else 0.0,
        "faithfulness": faithfulness,
        "faithfulness_measured": faithfulness is not None,
        "mean_cost_usd_e5": mean_cost,
        "baseline_cost_usd_e5": baseline,
        "cost_regression": cost_regression(mean_cost, baseline),
        "rows": rows,
    }
    LAST_RUN_PATH.write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _answer_text(raw: Any) -> str:
    """Pull the answer text out of the serialised FinalAnswer.

    :param raw: The ``answer`` state slot - a JSON string, or ``None`` when synthesis never ran.
    :returns: The answer text, or an empty string.
    """
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return str(raw)
    return str(parsed.get("text", "")) if isinstance(parsed, dict) else str(raw)


def hydrate_contexts(docs: list[dict[str, Any]]) -> list[str]:
    """Fetch the retrieved chunks' TEXT, which is what faithfulness is actually judged against.

    **Why this function has to exist.** The graph's state carries three fields per document -
    ``chunk_id``, ``doc_id``, ``score`` - and deliberately not the chunk text: every state slot
    is serialised into a Postgres checkpoint row on every super-step, so carrying the corpus
    through would make each checkpoint hundreds of kilobytes of prose nothing reads. That is the
    right decision for the graph and a silent disaster for the eval, which was passing
    ``["taxpayer-001", "taxpayer-014"]`` to RAGAS as the "contexts" the answer should be
    grounded in. Faithfulness asks whether each claim in the answer is supported by the
    retrieved context; against a list of identifiers, NOTHING is supported, so the metric would
    have scored a perfectly grounded answer near zero - and the gate would have failed the build
    for an answer-quality regression that never happened. An eval can be wrong in the failing
    direction too, and that kind is only marginally easier to catch.

    So the text is fetched back at scoring time, from the same corpus the retriever read, by the
    same DSN the retriever used. Scoring time rather than node time on purpose: this is eval
    surface, it runs once per scenario at the end, and the checkpoint stays small.

    :param docs: The pre-shaped documents from the ``docs`` state slot.
    :returns: One chunk of text per document, in retrieval order. Empty when the corpus is
        unreachable or the chunks are missing - which makes the row unscorable rather than
        scored against nothing, and shows up as an unmeasured metric rather than a bad one.
    """
    dsn = os.environ.get("TAXCALC_AI_PG_DSN")
    if not dsn or not docs:
        return []

    # (doc_id, chunk_idx) rather than the database's own chunk_id: the pipeline's chunk_id is the
    # synthetic `chunk-{doc_id}-p{chunk_idx}` string from taxcalc_ai.chunker, while the column of
    # that name is a BIGSERIAL row handle. They are different identifiers that share a name.
    keys: list[tuple[str, int]] = []
    for d in docs:
        chunk_id = str(d.get("chunk_id", ""))
        doc_id = str(d.get("doc_id", ""))
        idx = 0
        if chunk_id.startswith("chunk-") and "-p" in chunk_id:
            tail = chunk_id.rsplit("-p", 1)[1]
            if tail.isdigit():
                idx = int(tail)
        if doc_id:
            keys.append((doc_id, idx))
    if not keys:
        return []

    try:
        import psycopg

        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id, chunk_idx, chunk_text FROM doc_chunks "
                "WHERE (doc_id, chunk_idx) IN (SELECT * FROM unnest(%s::text[], %s::int[]))",
                ([k[0] for k in keys], [k[1] for k in keys]),
            )
            found = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    except Exception:
        # An unreachable corpus is an unmeasured metric, not an unfaithful answer. Same rule as
        # score_with_ragas, for the same reason.
        return []

    return [found[k] for k in keys if k in found]


def score_with_ragas(rows: list[dict[str, Any]], api_key: str | None = None) -> float | None:
    """Score answer faithfulness against the retrieved contexts.

    Returns ``None`` - never a default number - when RAGAS cannot run. This is the W7 D3 lesson
    applied directly: a gate that substitutes a passing default for an unmeasured metric reports
    a green build for a measurement that never happened, and nobody finds out until the metric
    they thought was protected has been drifting for weeks. ``None`` propagates into the summary
    as ``faithfulness_measured: false``, and the gate refuses it unless the caller has explicitly
    and loudly waived it.

    **The judge is passed explicitly, and that is not a stylistic preference.** ``evaluate(
    dataset, metrics=[...])`` with nothing else lets RAGAS construct its own default evaluator,
    and that default is OpenAI. A CI job holding only an Anthropic key therefore does not grade
    against Claude - it fails on an OpenAI authentication error, which this function converts to
    ``None``, which fails the gate for a reason that looks nothing like its cause. Worse, if an
    ``OPENAI_API_KEY`` happens to be in the environment it grades successfully against a provider
    nobody chose and bills them for it. taxcalc-ai's ``eval/run_ragas.py`` documents the same
    trap; this is the second project to need it written down.

    claude-haiku-4-5 as the judge: it is grading answers rather than producing them, and the
    evaluator is called several times per row, so the model choice is most of what this costs.

    :param rows: The per-scenario rows from :func:`run_eval`.
    :param api_key: The Anthropic credential for the judge. ``None`` falls back to
        langchain-anthropic's own ``ANTHROPIC_API_KEY`` lookup, which is the right behaviour for
        an ad-hoc local call and the wrong one for CI - see :func:`run_eval`.
    :returns: Mean faithfulness in 0-1, or ``None`` when it could not be measured.
    """
    try:
        from datasets import Dataset
        from langchain_anthropic import ChatAnthropic
        from ragas import evaluate
        from ragas.llms.base import LangchainLLMWrapper
        from ragas.metrics._faithfulness import Faithfulness
        from ragas.run_config import RunConfig
    except ImportError:
        return None

    scorable = [r for r in rows if r["answer"] and r["contexts"]]
    if not scorable:
        # No grounded answers to judge. Reported as unmeasured rather than as 0.0: a zero would
        # read as "the answers were unfaithful", which is a different and much more alarming
        # claim than "there was nothing to score".
        return None

    dataset = Dataset.from_list(
        [
            {
                "question": r["question"],
                "answer": r["answer"],
                "contexts": [str(c) for c in r["contexts"]],
            }
            for r in scorable
        ]
    )
    try:
        # The suppression below is narrow and on purpose. RAGAS 0.2 is typed
        # against langchain-core 0.3's `BaseLanguageModel[BaseMessage]`; langchain-core 1.x
        # re-parameterised that generic, so a perfectly valid ChatAnthropic no longer satisfies
        # the annotation. The runtime contract RAGAS actually uses - `agenerate_prompt` - is
        # unchanged, and is exercised by the probe recorded in PR_BODY.md: with a dummy key the
        # call reaches ANTHROPIC and returns 401, which is the assertion that matters here
        # (the judge is Claude, not RAGAS's OpenAI default).
        chat = ChatAnthropic(model="claude-haiku-4-5", timeout=120, api_key=api_key)
        evaluator = LangchainLLMWrapper(chat)  # type: ignore[arg-type]
        result = evaluate(
            dataset,
            metrics=[Faithfulness()],
            llm=evaluator,
            # max_retries=3, not the default 10: against a dead evaluator the defaults mean the
            # job exhausts ten retries with backoff before reporting anything knowable in the
            # first few seconds. Borrowed from taxcalc-ai, which measured that at 13m40s.
            run_config=RunConfig(max_retries=3, max_wait=8, timeout=60),
        )
    except Exception:
        # An evaluator that could not run - no credential, a rate limit, a transport failure -
        # is an unmeasured metric, not a failed one.
        return None

    return _mean_faithfulness(result)


def _mean_faithfulness(result: Any) -> float | None:
    """Pull the mean faithfulness out of whatever shape RAGAS returned.

    RAGAS has returned this as a scalar in some versions, a per-row list in others, and an
    ``EvaluationResult`` whose numbers live in ``to_pandas()`` in others again - and the
    differences are silent, so a version bump turns the gate into a crash rather than a score.
    All three are handled; anything else is reported as unmeasured rather than guessed at.

    **NaN is unmeasured, and this is the sharpest edge in the file.** RAGAS does not raise when
    the judge fails: it catches the exception per job, prints `Exception raised in Job[0]` to
    stderr, and returns a result whose score is `nan`. Measured directly, with a deliberately
    invalid key: `evaluate(...)` returned normally, the mean came back `nan`, and `nan < 0.85` is
    **False** - so an evaluator that answered nothing at all would have produced a PASSING gate.
    That is the exact failure this whole module argues against, hiding inside the metric it was
    written to protect. NaN therefore becomes `None`, which the gate refuses.

    :param result: Whatever :func:`ragas.evaluate` returned.
    :returns: Mean faithfulness, or ``None`` when it cannot be read or was not measured.
    """
    frame = None
    to_pandas = getattr(result, "to_pandas", None)
    if callable(to_pandas):
        try:
            frame = to_pandas()
        except Exception:
            # Not a frame-shaped result after all; the mapping shapes below may still work, so
            # this is a fall-through rather than a failure.
            frame = None
    if frame is not None and "faithfulness" in getattr(frame, "columns", ()):
        return _measured(float(frame["faithfulness"].mean()))

    try:
        score = result["faithfulness"]
    except (TypeError, KeyError, IndexError):
        return None
    if isinstance(score, list):
        values = [float(s) for s in score]
        return _measured(sum(values) / len(values)) if values else None
    try:
        return _measured(float(score))
    except (TypeError, ValueError):
        return None


def _measured(value: float) -> float | None:
    """Reject a NaN score as unmeasured.

    :param value: A mean faithfulness.
    :returns: The value, or ``None`` when it is NaN - see :func:`_mean_faithfulness`.
    """
    return None if math.isnan(value) else value
