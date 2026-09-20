# taxcalc-agent-svc/src/taxcalc_agent_svc/deps.py
"""Per-request dependencies, and why they travel on the config rather than in the state.

Two objects have to reach every node and belong to *one request*: the MCP
:class:`~mcp.ClientSession` the api node calls tools through, and the
:class:`~taxcalc_agent_svc.budgets.BudgetGuard` all three nodes charge against.

The obvious place to put them is the graph state, and it is the wrong place - **measurably**, not
as a matter of taste. Every declared state slot is serialised into a checkpoint row on every
super-step, and LangGraph's checkpoint serde is msgpack-based. Handing it a live object is not a
large row or a slow write; it is a hard failure::

    >>> JsonPlusSerializer().dumps_typed({"sess": socket.socket()})
    TypeError: Type is not msgpack serializable: socket

An MCP ``ClientSession`` owns exactly such a live transport, so a ``__mcp_session`` state slot
takes down every request the moment a real ``PostgresSaver`` is attached - and passes cleanly in
any test that compiled without a checkpointer, which is the worst possible place for the
difference to show up.

``config["configurable"]`` is the mechanism LangGraph provides for this, and it has the right
properties by construction: it is per-invocation, it reaches every node, and it is **not**
checkpointed. Verified the same way - a node declared ``(state, config)`` receives the caller's
``configurable`` entries alongside LangGraph's own ``__pregel_*`` bookkeeping.

The secondary benefit is a real one: a budget guard in the state would be *reducer-merged* across
the parallel fan-out, and there is no sensible reducer for "two references to the same mutable
tally". On the config it is one object both workers charge, which is what a per-request ceiling
has to be.
"""

from __future__ import annotations

from typing import Any, Final, Protocol

from langchain_core.runnables import RunnableConfig

from taxcalc_agent_svc.budgets import BudgetGuard
from taxcalc_agent_svc.state import AgentState


class AgentNode(Protocol):
    """The shape LangGraph's ``add_node`` accepts for an async node that wants the run config.

    A :class:`~typing.Protocol` and not a ``Callable[...]`` alias, and the difference is not
    stylistic. LangGraph's ``add_node`` overloads are written against ``_NodeWithConfig``, a
    protocol whose ``__call__`` declares the parameters **by name** (``state``, ``config``). A
    ``Callable[[AgentState, RunnableConfig], ...]`` describes positional-only parameters, which
    is not assignable to that protocol - so every ``add_node`` call fails to match any overload
    under ``--strict`` while running perfectly at runtime. Matching the protocol's shape here
    means the three node factories are checked against what LangGraph actually requires.

    Spelled once because all three factories return it, and three copies of a signature is three
    places for a change to half-land.
    """

    async def __call__(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Run one node.

        :param state: The graph state.
        :param config: The run config carrying the request's per-request dependencies.
        :returns: The node's partial state contribution.
        """
        ...


#: ``configurable`` key holding the request's MCP client session.
MCP_SESSION_KEY: Final[str] = "mcp_session"
#: ``configurable`` key holding the request's budget guard.
BUDGET_GUARD_KEY: Final[str] = "budget_guard"


def budget_guard(config: RunnableConfig | None) -> BudgetGuard:
    """Read the request's budget guard off the LangGraph config.

    Raises rather than defaulting to a fresh guard. A missing guard means the caller did not
    build its config through :func:`taxcalc_agent_svc.graph.run_config`, and quietly substituting
    a new one would give that request a *full* budget on every node - a silently unbounded run,
    which is the one failure this object exists to prevent.

    :param config: The LangGraph config passed to the node.
    :returns: The request's guard.
    :raises KeyError: when no guard was supplied.
    """
    configurable = (config or {}).get("configurable") or {}
    guard = configurable.get(BUDGET_GUARD_KEY)
    if not isinstance(guard, BudgetGuard):
        raise KeyError(
            f"{BUDGET_GUARD_KEY!r} missing from config['configurable']; "
            "build the config with taxcalc_agent_svc.graph.run_config"
        )
    return guard


async def open_session(config: RunnableConfig | None) -> Any:
    """Resolve the request's MCP session, awaiting a provider if one was supplied.

    Two shapes are accepted on purpose, and the flexibility is the point rather than laziness:

    * a **live session**, which is what the tests pass - a stub exposing ``list_tools`` and
      ``call_tool``, so a unit test of argument shaping needs no transport at all;
    * a **zero-argument async provider**, which is what the FastAPI app passes -
      :meth:`taxcalc_agent_svc.runtime.Dependencies.session`, so the connection is opened on the
      first request that actually needs a tool rather than on every request.

    Without the second shape the app would have to await the MCP session before entering the
    graph, which reintroduces the eager-connection defect one layer down: a docs-only question
    routed entirely to ``retrieval_agent`` would fail because a dependency it never touches was
    unreachable.

    :param config: The LangGraph config passed to the node.
    :returns: The session.
    :raises KeyError: when no session or provider was supplied.
    """
    candidate = mcp_session(config)
    if callable(candidate):
        return await candidate()
    return candidate


def mcp_session(config: RunnableConfig | None) -> Any:
    """Read the request's MCP client session off the LangGraph config.

    Returns the raw value - a session or a provider. Callers that need a usable session should
    use :func:`open_session`, which resolves both shapes.

    Typed as :data:`~typing.Any` rather than ``ClientSession`` on purpose: the api node's tests
    drive it with a stub session exposing ``list_tools`` and ``call_tool``, and requiring the
    concrete SDK class here would force every one of those tests to stand up a real transport to
    assert on argument shaping.

    :param config: The LangGraph config passed to the node.
    :returns: The request's session.
    :raises KeyError: when no session was supplied - the api node cannot invent one, and a
        ``None`` would surface as an ``AttributeError`` three frames deeper.
    """
    configurable = (config or {}).get("configurable") or {}
    if MCP_SESSION_KEY not in configurable:
        raise KeyError(
            f"{MCP_SESSION_KEY!r} missing from config['configurable']; "
            "build the config with taxcalc_agent_svc.graph.run_config"
        )
    return configurable[MCP_SESSION_KEY]
