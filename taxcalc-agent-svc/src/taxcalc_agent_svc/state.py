# taxcalc-agent-svc/src/taxcalc_agent_svc/state.py
"""AgentState: the typed slots the three-node graph reads and writes.

**Every merge-prone slot carries an explicit reducer, and that is the single most load-bearing
decision in this package.** The supervisor fans out to ``retrieval_agent`` and ``api_agent`` in
parallel; LangGraph collects both of their returned partial states and applies them to the same
channel. A slot declared as a bare type (``docs: list[dict]``) uses the default channel, whose
update rule is *last write wins* - so whichever worker finished second would silently erase the
other's contribution, and the synthesis node would answer from half the evidence with no error
anywhere. The failure is invisible: no exception, no log line, just an answer that omits what the
other agent found. Annotating the slot with a reducer is what makes the fan-out additive.

The three slots that need one, and why each takes the reducer it does:

``messages`` - :func:`~langgraph.graph.message.add_messages`
    The SDK's own reducer, which appends while de-duplicating on message id. Plain concatenation
    would double every message on a checkpoint resume, because the resumed run replays the
    channel writes that were already persisted.

``docs`` - :func:`operator.add`
    Retrieval contributes a list and nothing else writes it, so list concatenation is exactly
    right and needs no custom merge.

``tool_results`` - :func:`_merge_tool_results`
    A mapping, so ``operator.add`` does not apply (``dict + dict`` raises). Merged key-wise so
    that a partial failure on one fan-out leg preserves the other leg's results instead of
    dropping them alongside its own.

``cost_usd_e5`` - :func:`operator.add`
    Integer minor units summed across every node that spent money. This is the W6 D4 money
    discipline carried forward: cost is an ``int`` in 1e-5 USD, never a ``float``. Two agents
    each spending 0.1 cents must total 0.2 cents, and a last-write-wins scalar would report the
    cheaper of the two as the whole request's cost - under-reporting spend is the one direction a
    budget must never round.

``visited_nodes`` - :func:`operator.add`
    Appended by each node as it runs; the trajectory eval reads it to assert the golden node
    sequence. See :data:`VISITED_NODES` for why this is a real state slot rather than something
    recovered from LangGraph internals.

The terminal scalars (``answer``, ``question``, ``tenant_id``, ``thread_id``) are deliberately
bare. ``answer`` is written exactly once, by synthesis, and overwriting on purpose is the whole
point: a reducer there would concatenate a re-run's answer onto the first one.
"""

# NOTE: deliberately NO `from __future__ import annotations` in this module, and it is the one
# module in the package without it.
#
# That import turns every annotation into a string, so `AgentState.__annotations__["docs"]` comes
# back as a `ForwardRef` with no `__metadata__` - the reducer is still applied at runtime (both
# LangGraph and `typing.get_type_hints` resolve it), but it is no longer *readable* from the
# class. The reducers are the load-bearing decision in this package and the thing most worth
# asserting directly, so the annotations stay eager and the assertion stays a one-liner rather
# than a `get_type_hints(..., include_extras=True)` incantation in every test and reviewer's REPL.
#
# The cost is zero here: every annotation below is valid at runtime on 3.12 without the import.

import operator
from typing import Annotated, Any, Final, TypedDict

from langgraph.graph.message import add_messages

#: Name of the state slot each node appends itself to.
#:
#: **Why this exists at all.** The obvious way to assert a trajectory is to read the nodes
#: LangGraph visited out of the final state - and there is no such key. LangGraph's channel
#: layout carries the state slots the graph declares plus its own bookkeeping channels
#: (``__start__``, ``branch:*``), none of which is an ordered record of which node bodies
#: actually ran. A checkpoint's ``metadata.writes`` comes closest, but it is per-super-step, is
#: an implementation detail of the checkpointer, and is empty for a graph compiled without one -
#: so an eval written against it would pass only in the configuration that has a database.
#:
#: Making the trajectory an explicit, reduced state slot costs each node one line and buys an
#: assertion that holds for every caller: ``invoke``, ``astream_events``, with a checkpointer and
#: without.
VISITED_NODES: Final[str] = "visited_nodes"


def _merge_tool_results(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """Merge two ``tool_results`` contributions key-wise, last write winning per key.

    **Why not ``operator.add``.** Mappings do not support ``+``; a bare ``operator.add`` on this
    slot raises ``TypeError: unsupported operand type(s) for +: 'dict' and 'dict'`` the first
    time two nodes write it - at runtime, inside a fan-in, which is the worst place to discover a
    channel definition is wrong.

    **Why key-wise rather than replacing.** The api node is not the only writer forever; the
    moment a second tool-using node exists, or the api node is retried after a partial failure,
    the surviving contribution must not be discarded. Merging per key means a leg that failed
    after recording two of its three tool calls keeps those two.

    ``None`` is tolerated on both sides because a node that returns no ``tool_results`` key at
    all leaves the channel's current value as ``None`` on the first super-step, and a reducer
    that raised on that would make "this node had nothing to contribute" an error.

    :param old: The channel's current value, or ``None`` before anything has written it.
    :param new: The incoming contribution, or ``None``.
    :returns: A new mapping holding both sides' keys.
    """
    return {**(old or {}), **(new or {})}


class AgentState(TypedDict, total=False):
    """The graph's typed state.

    ``total=False`` because the graph is entered with only ``question``, ``tenant_id`` and
    ``thread_id`` set - the remaining slots are filled by the nodes as they run, and declaring
    them required would make every partial state a type error at the one place partial states are
    the normal case.

    :ivar question: The user's question. Set once by the HTTP handler; read by every node.
    :ivar tenant_id: The requesting tenant. A security boundary, not a label: it is the
        ``X-Tenant`` header on every MCP tool call and the corpus pre-filter on retrieval.
    :ivar thread_id: The checkpoint thread. Pinned on every call site so ``PostgresSaver``
        writes a resumable row rather than an orphan.
    :ivar messages: Conversation history, appended by :func:`~langgraph.graph.message.add_messages`.
    :ivar docs: Retrieved chunks, pre-shaped to ``chunk_id``/``doc_id``/``score``.
    :ivar tool_results: MCP tool name -> that tool's raw result.
    :ivar answer: The Instructor-typed :class:`~taxcalc_agent_svc.nodes.synthesis.FinalAnswer`,
        serialised as JSON. Written once, by synthesis.
    :ivar cost_usd_e5: Cumulative spend in integer 1e-5 USD minor units.
    :ivar visited_nodes: The node names that ran, in order. See :data:`VISITED_NODES`.
    """

    # Inputs, set once by the HTTP handler:
    question: str
    tenant_id: str
    thread_id: str

    # Conversation history (appended, never overwritten):
    messages: Annotated[list[Any], add_messages]

    # Parallel-fan-out slots - independent reducers so one leg cannot erase the other's work:
    docs: Annotated[list[dict[str, Any]], operator.add]
    tool_results: Annotated[dict[str, Any], _merge_tool_results]

    # Terminal scalar (overwritten once, by synthesis, on purpose):
    answer: str | None

    # Cumulative cost in 1e-5 USD minor units (W6 D4 money discipline: int, never float):
    cost_usd_e5: Annotated[int, operator.add]

    # Ordered record of which node bodies ran; the trajectory eval asserts against it:
    visited_nodes: Annotated[list[str], operator.add]
