# taxcalc-agent-svc/tests/test_graph_compile.py
"""The graph has three named nodes, the declared reducers, and a checkpointer when asked for one.

These are topology assertions, not behaviour assertions: no node body runs here. That is the
point - the things being checked (a reducer is attached to the right channel, a node name matches
what the eval's golden trajectory expects, a checkpointer is wired) are exactly the things that
break silently at runtime with no exception anywhere.
"""

from __future__ import annotations

import operator

from langgraph.checkpoint.memory import MemorySaver

from taxcalc_agent_svc.graph import API_AGENT, RETRIEVAL_AGENT, SYNTHESIS_AGENT, build_graph
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.state import AgentState, _merge_tool_results


def test_graph_compiles_with_exactly_the_three_named_nodes(settings: Settings) -> None:
    """The compiled graph publishes retrieval_agent, api_agent and synthesis_agent.

    The names are asserted, not just the count. The eval suite's twenty golden trajectories name
    these strings, and so does the SSE bridge's ``on_chain_end`` filter; a rename that updated
    the graph but not those would produce a service that runs and an eval that can never pass.
    """
    graph = build_graph(settings)
    nodes = set(graph.get_graph().nodes)
    assert {RETRIEVAL_AGENT, API_AGENT, SYNTHESIS_AGENT} <= nodes


def test_docs_slot_carries_an_additive_reducer() -> None:
    """``docs`` is Annotated with operator.add, so a parallel fan-out concatenates.

    Read off ``__annotations__`` rather than inferred from behaviour because the failure being
    guarded is *silent*: a bare ``list[dict]`` runs fine, produces no error, and simply loses one
    worker's contribution. Only the annotation distinguishes the two.
    """
    meta = AgentState.__annotations__["docs"].__metadata__
    assert operator.add in meta


def test_tool_results_slot_carries_the_dict_merger() -> None:
    """``tool_results`` is Annotated with the key-wise merger, not operator.add.

    ``operator.add`` on a mapping raises ``TypeError`` at fan-in, at runtime. This assertion is
    what keeps that from being discovered in production.
    """
    meta = AgentState.__annotations__["tool_results"].__metadata__
    assert _merge_tool_results in meta


def test_cost_is_summed_across_nodes_not_overwritten() -> None:
    """``cost_usd_e5`` accumulates, so two agents' spend totals instead of one shadowing."""
    meta = AgentState.__annotations__["cost_usd_e5"].__metadata__
    assert operator.add in meta


def test_merge_tool_results_preserves_both_legs() -> None:
    """A partial failure on one fan-out leg keeps the other leg's results."""
    merged = _merge_tool_results({"orders.get_order": 1}, {"rag.retrieve_and_generate": 2})
    assert merged == {"orders.get_order": 1, "rag.retrieve_and_generate": 2}


def test_merge_tool_results_tolerates_an_empty_channel() -> None:
    """A node that contributes nothing is not an error - the channel starts as None."""
    assert _merge_tool_results(None, {"a": 1}) == {"a": 1}
    assert _merge_tool_results({"a": 1}, None) == {"a": 1}


def test_checkpointer_is_attached_when_supplied(settings: Settings) -> None:
    """A compiled graph carries the checkpointer it was given.

    MemorySaver is used HERE, in a test that only asserts the wiring, precisely because the
    production path must never use one - :func:`build_taxcalc_agent_graph` is the entry point
    that guarantees Postgres, and the CI gate greps ``src/`` (not ``tests/``) for MemorySaver.
    """
    cp = MemorySaver()
    graph = build_graph(settings, checkpointer=cp)
    assert graph.checkpointer is cp


def test_compiling_without_a_checkpointer_is_allowed_for_tests(settings: Settings) -> None:
    """``checkpointer=None`` compiles, for the schema tests - and carries no saver."""
    graph = build_graph(settings)
    assert not graph.checkpointer
