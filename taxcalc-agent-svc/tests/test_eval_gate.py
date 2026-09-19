# taxcalc-agent-svc/tests/test_eval_gate.py
"""The gate's arithmetic, tested directly rather than only exercised by a red build.

``verdict`` is pure and separated from the run for exactly this reason. A threshold that is only
ever exercised by CI failing is a threshold nobody has checked in the passing direction - and the
most expensive bug a gate can have is being permanently green.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from trajectory import (
    COST_REGRESSION_LIMIT,
    FAITHFULNESS_FLOOR,
    TRAJECTORY_FLOOR,
    Scenario,
    cost_regression,
    load_scenarios,
    trajectory_match,
)

from taxcalc_agent_svc.scripts.eval import verdict


def _summary(**over: Any) -> dict[str, Any]:
    """A passing summary, with fields overridden per test.

    :param over: Fields to override.
    :returns: A summary shaped like :func:`trajectory.run_eval`'s output.
    """
    base: dict[str, Any] = {
        "scenarios": 20,
        "trajectory_match": 1.0,
        "answer_substring_rate": 1.0,
        "faithfulness": 0.95,
        "faithfulness_measured": True,
        "mean_cost_usd_e5": 500,
        "baseline_cost_usd_e5": 500,
        "cost_regression": 0.0,
        "rows": [],
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- trajectory match


def test_a_matching_trajectory_scores_one() -> None:
    """Every expected node ran."""
    assert trajectory_match(("retrieval_agent", "synthesis_agent"), ("retrieval_agent",)) == 1.0


def test_a_missing_node_scores_zero_not_a_fraction() -> None:
    """Two of three expected nodes is not two-thirds of a success.

    Averaging partial trajectories to 0.67 would let a suite of runs that each skipped an
    evidence source clear a 0.70 floor - which is the precise failure this binary score avoids.
    """
    actual = ("retrieval_agent", "synthesis_agent")
    expected = ("retrieval_agent", "api_agent", "synthesis_agent")
    assert trajectory_match(actual, expected) == 0.0


def test_an_extra_node_still_matches() -> None:
    """Subset, not equality: a graph that grows a node should not fail twenty scenarios.

    A topology change that does MORE work on the way to the same answer is not a regression; one
    that stops visiting an expected node is. Equality would conflate them.
    """
    actual = ("retrieval_agent", "guardrail_agent", "synthesis_agent")
    assert trajectory_match(actual, ("retrieval_agent", "synthesis_agent")) == 1.0


# ------------------------------------------------------------------------------ cost regression


def test_cost_regression_is_fractional_and_signed() -> None:
    """A 20% increase reports 0.20; a decrease reports negative."""
    assert cost_regression(600, 500) == pytest.approx(0.20)
    assert cost_regression(400, 500) == pytest.approx(-0.20)


def test_a_first_run_has_no_baseline_and_does_not_regress() -> None:
    """Reporting an infinite regression would fail a build for being the first one."""
    assert cost_regression(500, 0) == 0.0


# -------------------------------------------------------------------------------- the verdict


def test_a_healthy_run_passes() -> None:
    """The passing direction, which a gate exercised only by failures never checks."""
    passed, failures = verdict(_summary(), allow_unmeasured=False)
    assert passed
    assert failures == []


def test_a_routing_regression_fails() -> None:
    """Trajectory below the floor is a failure that names the metric and the floor."""
    passed, failures = verdict(
        _summary(trajectory_match=TRAJECTORY_FLOOR - 0.01), allow_unmeasured=False
    )
    assert not passed
    assert "trajectory match" in failures[0]


def test_the_trajectory_floor_is_inclusive() -> None:
    """Exactly at the floor passes; the failure condition is strictly below it."""
    passed, _ = verdict(_summary(trajectory_match=TRAJECTORY_FLOOR), allow_unmeasured=False)
    assert passed


def test_an_answer_quality_regression_fails() -> None:
    """Faithfulness below the floor is a failure."""
    passed, failures = verdict(
        _summary(faithfulness=FAITHFULNESS_FLOOR - 0.01), allow_unmeasured=False
    )
    assert not passed
    assert "faithfulness" in failures[0]


def test_an_unmeasured_faithfulness_fails_by_default() -> None:
    """An unmeasured metric is NOT a passing one.

    The W7 D3 lesson, paid for once already in this repository: a skip that renders as a green
    tick is indistinguishable from a measurement that passed, and the metric quietly stops being
    protected.
    """
    passed, failures = verdict(
        _summary(faithfulness=None, faithfulness_measured=False), allow_unmeasured=False
    )
    assert not passed
    assert "NOT MEASURED" in failures[0]


def test_an_unmeasured_faithfulness_can_be_waived_deliberately() -> None:
    """The escape hatch exists for fork PRs with no secrets - and only when asked for."""
    passed, _ = verdict(
        _summary(faithfulness=None, faithfulness_measured=False), allow_unmeasured=True
    )
    assert passed


def test_a_cost_regression_fails_even_when_quality_improved() -> None:
    """The gate no quality metric can replace.

    A prompt that stuffs the whole corpus into context scores BETTER on faithfulness while
    tripling the bill. Without this gate that lands as an improvement.
    """
    passed, failures = verdict(
        _summary(faithfulness=0.99, cost_regression=0.30), allow_unmeasured=False
    )
    assert not passed
    assert "cost per run regressed" in failures[0]


def test_cost_exactly_at_the_limit_passes() -> None:
    """The limit is inclusive; only strictly beyond it fails."""
    passed, _ = verdict(_summary(cost_regression=COST_REGRESSION_LIMIT), allow_unmeasured=False)
    assert passed


def test_several_regressions_are_all_reported() -> None:
    """One run can break in more than one way, and the reader wants all of them.

    Reporting only the first would turn one red build into three sequential ones.
    """
    passed, failures = verdict(
        _summary(trajectory_match=0.1, faithfulness=0.1, cost_regression=0.9),
        allow_unmeasured=False,
    )
    assert not passed
    assert len(failures) == 3


# ------------------------------------------------------------------------------- the suite itself


def test_the_committed_suite_has_twenty_rows() -> None:
    """The brief's size, asserted so a truncated file cannot quietly shrink the gate."""
    assert len(load_scenarios()) == 20


def test_every_scenario_expects_synthesis() -> None:
    """Every path ends at synthesis; a scenario that did not would be unreachable."""
    assert all("synthesis_agent" in sc.expected_nodes for sc in load_scenarios())


def test_the_suite_covers_all_three_routing_branches() -> None:
    """Docs-only, api-only and both.

    A suite that exercised one branch twenty times would report a healthy trajectory score while
    leaving two thirds of the router unchecked.
    """
    shapes = {sc.expected_nodes for sc in load_scenarios()}
    assert ("retrieval_agent", "synthesis_agent") in shapes
    assert ("api_agent", "synthesis_agent") in shapes
    assert ("retrieval_agent", "api_agent", "synthesis_agent") in shapes


def test_qids_are_unique() -> None:
    """The qid is the checkpoint thread id: a duplicate would make two scenarios share state."""
    qids = [sc.qid for sc in load_scenarios()]
    assert len(qids) == len(set(qids))


def test_scenarios_are_frozen() -> None:
    """A harness that could mutate its own expectations is a harness that can make itself pass."""
    sc = load_scenarios()[0]
    with pytest.raises(dataclasses.FrozenInstanceError, match="cannot assign"):
        sc.question = "something else"  # type: ignore[misc]


def test_the_supervisor_routes_every_committed_scenario_as_expected() -> None:
    """The routing table and the golden trajectories agree - checked without running the graph.

    This is the fast half of the trajectory gate, and it needs neither a database nor a model.
    A keyword added to the supervisor that silently re-routes an existing scenario fails here, in
    milliseconds, rather than in the full eval run.
    """
    from taxcalc_agent_svc.graph import SYNTHESIS_AGENT, supervisor
    from taxcalc_agent_svc.state import AgentState

    for sc in load_scenarios():
        state = AgentState(question=sc.question, tenant_id=sc.tenant_id, thread_id=sc.qid)
        routed = {s.node for s in supervisor(state)}
        expected = set(sc.expected_nodes) - {SYNTHESIS_AGENT}
        assert routed == expected, f"{sc.qid}: supervisor routed {routed}, suite expects {expected}"


def test_scenario_is_constructible_directly() -> None:
    """The dataclass is usable without the JSONL, for an ad-hoc one-off run."""
    sc = Scenario("q1", "question", "tenant-a", ("retrieval_agent",), "sub")
    assert sc.qid == "q1"
