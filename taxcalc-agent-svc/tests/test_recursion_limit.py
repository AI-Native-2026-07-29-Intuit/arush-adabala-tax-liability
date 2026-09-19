# taxcalc-agent-svc/tests/test_recursion_limit.py
"""A synthetic feedback loop terminates on ``recursion_limit`` rather than running forever.

The graph shipped today is acyclic, so its recursion limit can never fire - which is exactly why
this test builds a *deliberately cyclic* graph with the same limit. The limit is insurance
against a feedback edge that does not exist yet (synthesis routing back to retrieval on low
confidence is the one already sketched), and insurance that has never been fired is insurance
nobody knows is broken.

The two caps are tested against each other here as well. ``recursion_limit`` is the FAST budget:
it bounds *turns*, so it catches a loop that spends nothing. :class:`BudgetGuard` is the SLOW
budget: it bounds *dollars*, so it catches a run that is progressing but expensive. Neither
subsumes the other, and the pair is the whole defence-in-depth claim.
"""

from __future__ import annotations

from typing import Any, TypedDict

import pytest
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, StateGraph

from taxcalc_agent_svc.budgets import BudgetExceeded, BudgetGuard
from taxcalc_agent_svc.settings import Settings


class LoopState(TypedDict, total=False):
    """State for the synthetic loop.

    :ivar turns: How many times the looping node has run.
    """

    turns: int


def _looping_graph(counter: list[int] | None = None) -> Any:
    """Build a graph whose only node routes back to itself, forever.

    :param counter: A list the node appends to on each execution, so a test can count how many
        times the body actually ran. Counting executions rather than reading the exception's
        text is what makes the assertions below about behaviour instead of about a message.
    :returns: The compiled cyclic graph.
    """

    async def spin(state: LoopState) -> dict[str, int]:
        """Increment the turn counter.

        :param state: The loop state.
        :returns: The incremented counter.
        """
        if counter is not None:
            counter.append(1)
        return {"turns": state.get("turns", 0) + 1}

    sg: StateGraph[LoopState, None, LoopState, LoopState] = StateGraph(LoopState)
    sg.add_node("spin", spin)
    sg.add_edge(START, "spin")
    sg.add_edge("spin", "spin")  # the cycle
    return sg.compile()


async def test_a_feedback_loop_is_stopped_by_the_recursion_limit(settings: Settings) -> None:
    """A cyclic graph raises GraphRecursionError instead of looping indefinitely.

    The pinned limit comes from settings, not from LangGraph's default, which is the point of
    pinning it: a default that moves in a library release must not move this service's runaway
    protection with it.
    """
    graph = _looping_graph()
    with pytest.raises(GraphRecursionError):
        await graph.ainvoke({"turns": 0}, config={"recursion_limit": settings.recursion_limit})


async def test_it_stops_within_the_configured_number_of_super_steps() -> None:
    """The node body runs at most ``recursion_limit`` times before the loop is cut.

    Asserted as "at most" rather than "exactly": LangGraph counts the START super-step too, so
    the body runs one fewer time than the limit. Pinning a library's internal counting would
    make this a tripwire for a harmless refactor; pinning the bound tests the property that
    actually protects the service.
    """
    runs: list[int] = []
    graph = _looping_graph(runs)
    with pytest.raises(GraphRecursionError):
        await graph.ainvoke({"turns": 0}, config={"recursion_limit": 5})
    assert 0 < len(runs) <= 5


async def test_a_low_limit_does_strictly_less_work_than_a_high_one() -> None:
    """The limit is honoured as a VALUE, not merely present.

    A graph that raised on some hard-coded internal default would pass every test above. This
    one cannot be satisfied that way: with a limit of 3 the body must run strictly fewer times
    than with a limit of 20.
    """
    low: list[int] = []
    high: list[int] = []
    for limit, runs in ((3, low), (20, high)):
        with pytest.raises(GraphRecursionError):
            await _looping_graph(runs).ainvoke(
                {"turns": 0}, config={"recursion_limit": limit}
            )
    assert len(low) < len(high)


def test_the_dollar_cap_catches_what_the_turn_cap_cannot() -> None:
    """A run well inside the turn limit can still be far outside the budget.

    Three super-steps is trivially within a limit of 25, and three large calls exhaust a $0.25
    ceiling. This is the case that makes the second cap necessary rather than redundant.
    """

    class Resp:
        """A response carrying a large usage block."""

        class usage:
            """Anthropic-shaped usage."""

            input_tokens = 20_000
            output_tokens = 10_000

    guard = BudgetGuard(25_000)
    steps = 0
    with pytest.raises(BudgetExceeded):
        for _ in range(25):  # comfortably inside the turn cap
            guard.check_or_raise()
            guard.record_call(Resp())
            steps += 1
    assert steps < 25, "the dollar ceiling must fire before the turn cap is reached"


async def test_the_turn_cap_catches_what_the_dollar_cap_cannot(settings: Settings) -> None:
    """A loop that spends nothing is invisible to a budget and caught by the turn cap.

    The looping node makes no model call at all, so the guard's tally never moves. Only the
    recursion limit ends this run - which is the other half of the defence-in-depth claim.
    """
    guard = BudgetGuard(25_000)
    runs: list[int] = []
    with pytest.raises(GraphRecursionError):
        await _looping_graph(runs).ainvoke(
            {"turns": 0}, config={"recursion_limit": settings.recursion_limit}
        )
    assert runs, "the loop must actually have run"
    assert guard.spent_usd_e5 == 0, "a free loop is invisible to a dollar ceiling"
