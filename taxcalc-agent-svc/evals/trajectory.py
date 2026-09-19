# taxcalc-agent-svc/evals/trajectory.py
"""Trajectory eval: twenty scenarios, each asserting a golden node sequence and an answer floor.

**Why a trajectory eval rather than only an answer eval.** An answer-quality score says whether
the final text was good; it says nothing about *how* the graph got there. A supervisor that
routed every question to both workers would produce perfectly good answers at twice the cost and
latency, and a faithfulness score alone would call that a healthy service. Conversely a routing
regression that sent a refund question to retrieval only would produce a confident, well-formed,
completely ungrounded answer - which scores well on fluency and is exactly the failure this file
exists to catch.

So each row asserts two independent things: the set of nodes that ran, and the quality of what
they produced. A regression in either fails the build.

**The match is subset, not equality, and that is a deliberate looseness.** ``trajectory_match``
returns 1.0 when the expected nodes are a subset of the visited ones. A graph that grows a fourth
node - a re-ranking pass, a guardrail - should not fail twenty scenarios for doing more work on
the way to the same answer; a graph that stops visiting an expected node should fail all of them.
Equality would make every legitimate topology change a twenty-row red build, which is how an eval
suite becomes something people delete rather than fix.

**Three gates, not one, and the third is the one usually missing.** Trajectory match catches
routing regressions. Faithfulness catches answer regressions. Cost-per-run catches the change
that improves both by spending three times as much - a prompt that stuffs the whole corpus into
context scores *better* on faithfulness while quietly tripling the bill, and no quality metric
will ever object. That is why the cost comparison is a gate and not a dashboard.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Where the committed scenarios live. Data, not code: adding a scenario is a JSONL line rather
#: than a Python edit, so the suite can grow without touching the harness that runs it.
SCENARIOS_PATH = Path(__file__).parent / "scenarios.jsonl"

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
    :ivar expected_nodes: The golden node sequence, as a subset expectation.
    :ivar expected_answer_substring: A token the answer should contain. A weak signal on its own,
        which is why it is *reported* rather than gated - see :func:`run_eval`.
    """

    qid: str
    question: str
    tenant_id: str
    expected_nodes: tuple[str, ...]
    expected_answer_substring: str


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[Scenario]:
    """Read the committed scenario suite.

    :param path: The JSONL file to read.
    :returns: The scenarios, in file order.
    """
    scenarios: list[Scenario] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        scenarios.append(
            Scenario(
                qid=row["qid"],
                question=row["question"],
                tenant_id=row["tenant_id"],
                expected_nodes=tuple(row["expected_nodes"]),
                expected_answer_substring=row["expected_answer_substring"],
            )
        )
    return scenarios


def trajectory_match(actual: tuple[str, ...], expected: tuple[str, ...]) -> float:
    """Score one run's node sequence against its golden one.

    :param actual: The nodes that ran, from the ``visited_nodes`` state slot.
    :param expected: The golden nodes.
    :returns: 1.0 when every expected node ran, 0.0 otherwise. Binary rather than a fraction
        because a partial trajectory is not a partial success: a run that visited two of three
        expected nodes produced an answer missing a whole evidence source, and averaging that to
        0.67 would let a suite of such runs clear a 0.70 floor.
    """
    return 1.0 if set(expected).issubset(set(actual)) else 0.0


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
    run_ns = uuid.uuid4().hex[:12]
    matched = 0
    answer_hits = 0
    total_cost_e5 = 0
    rows: list[dict[str, Any]] = []

    for sc in suite:
        guard = BudgetGuard(settings.cost_ceiling_usd_e5)
        thread_id = f"eval-{run_ns}-{sc.qid}"
        cfg = run_config(thread_id, settings, guard=guard, session=None)
        # ainvoke, not invoke: every node body is async, and the synchronous entry point raises
        # `TypeError: No synchronous function provided` before running any of them.
        state = await graph.ainvoke(
            {"question": sc.question, "tenant_id": sc.tenant_id, "thread_id": thread_id},
            config=cfg,
        )
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
                "contexts": [d.get("doc_id", "") for d in state.get("docs", [])],
                "question": sc.question,
            }
        )

    faithfulness = score_with_ragas(rows) if score_faithfulness else None
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


def score_with_ragas(rows: list[dict[str, Any]]) -> float | None:
    """Score answer faithfulness against the retrieved contexts.

    Returns ``None`` - never a default number - when RAGAS cannot run. This is the W7 D3 lesson
    applied directly: a gate that substitutes a passing default for an unmeasured metric reports
    a green build for a measurement that never happened, and nobody finds out until the metric
    they thought was protected has been drifting for weeks. ``None`` propagates into the summary
    as ``faithfulness_measured: false``, and the gate refuses it unless the caller has explicitly
    and loudly waived it.

    :param rows: The per-scenario rows from :func:`run_eval`.
    :returns: Mean faithfulness in 0-1, or ``None`` when it could not be measured.
    """
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import faithfulness
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
        result = evaluate(dataset, metrics=[faithfulness])
    except Exception:
        # An evaluator that could not run - no credential, a rate limit, a transport failure -
        # is an unmeasured metric, not a failed one.
        return None

    # RAGAS has returned this key as a scalar in some versions and a per-row list in others, and
    # the difference is silent: `float(a_one_element_list)` raises, and `float(a_long_list)`
    # raises too, so a version bump would turn the gate into a crash rather than a score. Both
    # shapes are handled, and anything else is reported as unmeasured rather than guessed at.
    try:
        score = result["faithfulness"]  # type: ignore[index]
    except (TypeError, KeyError):
        return None
    if isinstance(score, list):
        return sum(float(s) for s in score) / len(score) if score else None
    return float(score)
