# taxcalc-agent-svc/tests/test_supervisor.py
"""The supervisor returns a ``list[Send]``, and all three routing branches are covered.

All three, explicitly: docs-only, api-only, and both. A router tested on one branch is a router
whose other branches are discovered in production, and the "both" case is the one that exercises
the parallel fan-out the reducers exist for - so it is the branch most worth an assertion and the
easiest to leave out.

The fourth case - a question matching nothing - is tested too, because "route nowhere" is a
plausible-looking implementation that produces the single worst outcome available here: synthesis
with no evidence, which answers confidently and ungrounded.
"""

from __future__ import annotations

from langgraph.types import Send

from taxcalc_agent_svc.graph import API_AGENT, RETRIEVAL_AGENT, supervisor
from taxcalc_agent_svc.state import AgentState


def _state(question: str) -> AgentState:
    """Build a minimal state carrying one question.

    :param question: The question to route.
    :returns: A state the supervisor can read.
    """
    return AgentState(question=question, tenant_id="tenant-a", thread_id="t-1")


def _targets(sends: list[Send]) -> list[str]:
    """Extract the node names a fan-out plan targets.

    :param sends: The supervisor's plan.
    :returns: The target node names, in order.
    """
    return [s.node for s in sends]


def test_a_docs_question_routes_only_to_retrieval() -> None:
    """A policy question needs the corpus and not the order service."""
    plan = supervisor(_state("what is the policy on taxpayer returns"))
    assert _targets(plan) == [RETRIEVAL_AGENT]


def test_an_api_question_routes_only_to_the_api_agent() -> None:
    """An order-status question needs the tools and not the corpus."""
    plan = supervisor(_state("what is the status of order ord-synth-9001"))
    assert _targets(plan) == [API_AGENT]


def test_a_question_needing_both_fans_out_to_both_workers() -> None:
    """The parallel branch: both workers run, and both reach synthesis.

    This is the case the reducers exist for - two nodes writing the same state in one super-step.
    """
    plan = supervisor(_state("look up order ord-synth-9001 and tell me the refund policy"))
    assert _targets(plan) == [RETRIEVAL_AGENT, API_AGENT]


def test_an_unrecognised_question_grounds_rather_than_answering_blind() -> None:
    """An empty plan defaults to retrieval, never to an empty fan-out.

    Returning ``[]`` would send the graph straight to synthesis with no docs and no tool results,
    which hits the refusal path at best and produces a confident ungrounded answer at worst.
    """
    plan = supervisor(_state("hello"))
    assert _targets(plan) == [RETRIEVAL_AGENT]


def test_the_plan_is_made_of_Send_objects_not_bare_node_names() -> None:
    """``list[Send]``, not ``list[str]``.

    Names route with the *current* state implicitly; ``Send`` routes with an explicit payload,
    which is what later lets one worker be handed a narrowed view of the state without changing
    the graph's shape.
    """
    plan = supervisor(_state("look up order ord-synth-9001 and tell me the refund policy"))
    assert all(isinstance(s, Send) for s in plan)


def test_routing_is_case_insensitive() -> None:
    """The keyword table is lower-cased at comparison time, so shouting still routes."""
    assert _targets(supervisor(_state("STATUS OF ORDER ORD-SYNTH-9001"))) == [API_AGENT]
