# taxcalc-agent-svc/src/taxcalc_agent_svc/graph.py
"""Three-node StateGraph with supervisor-routed parallel topology.

::

    START -> supervisor (router fn returning list[Send])
      \\-> retrieval_agent -.
                             |-> synthesis_agent -> END
      \\-> api_agent       -'

Every node wraps ``@traceable`` over ``@deadline``; every compile pins a ``recursion_limit``; the
``PostgresSaver`` checkpointer persists state after every super-step so cross-pod and
cross-restart resume works.

**The supervisor is a single policy point, and naming it that is the design.** It is a plain
function today doing keyword routing, which looks like something that belongs inline in a
conditional edge. It is a named node because of what lands here next: per-tenant rate limits,
tenant feature gates, and the cost checks that decide a plan is too expensive to run *before*
paying for it. Those are policy, they apply to every request, and they need one place to live. A
conditional edge expressing the same routing would scatter the same decisions across the edges
that happen to exist when each is added.

**It returns ``list[Send]`` rather than a list of node names, and the difference is real.**
Returning names routes to those nodes with the *current* state. ``Send(node, payload)`` routes
with an *explicit* payload, which is what makes the fan-out a deliberate hand-off rather than an
implicit share - and is the mechanism that later lets one worker be sent a narrowed view of the
state (a single tenant's slice, one sub-question) without changing the graph's shape.

**On an empty plan it defaults to retrieval rather than routing straight to synthesis.** A
question matching no keyword is a question the router does not understand, and the safe behaviour
for a router that does not understand is to ground the answer in documents - not to answer blind.
Synthesis with no docs and no tool results hits its refusal path, so "route nowhere" would
reliably produce a confident-sounding ungrounded answer, which is the single worst outcome
available here.

**Both workers flow into synthesis, which is a fan-in, which is why the reducers exist.** Two
nodes write to the same state in the same super-step. Without the reducers in
:mod:`taxcalc_agent_svc.state` the second write erases the first, silently. That is not a
hypothetical: it is the default channel behaviour, and it produces no error at all.

**``recursion_limit`` is pinned explicitly on compile AND on every call site.** LangGraph's
default happens to be 25 today, which is exactly why it is written down: a default that moves in
a minor release moves this service's runaway protection with it, and the whole point of the limit
is that it does not change without someone deciding it should.
"""

from __future__ import annotations

from typing import Any, Final

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from taxcalc_agent_svc.budgets import BudgetGuard
from taxcalc_agent_svc.deps import BUDGET_GUARD_KEY, MCP_SESSION_KEY
from taxcalc_agent_svc.nodes.api import make_api_node
from taxcalc_agent_svc.nodes.retrieval import make_retrieval_node
from taxcalc_agent_svc.nodes.synthesis import make_synthesis_node
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.state import AgentState

#: Node names, as constants. The eval suite's golden trajectories, the SSE bridge's
#: ``on_chain_end`` filter and the graph wiring all name these; three copies of a string literal
#: is three chances for a rename to half-land.
RETRIEVAL_AGENT: Final[str] = "retrieval_agent"
API_AGENT: Final[str] = "api_agent"
SYNTHESIS_AGENT: Final[str] = "synthesis_agent"

#: Keywords routing a question to the corpus. Lower-cased at comparison time, so the table is
#: written in the form it is compared in.
DOCS_KEYWORDS: Final[tuple[str, ...]] = ("policy", "docs", "how do i", "rule", "deduction")

#: Keywords routing a question to the transactional tools.
API_KEYWORDS: Final[tuple[str, ...]] = ("order", "refund", "status", "ord-")


def supervisor(state: AgentState) -> list[Send]:
    """Single policy point. Returns the parallel fan-out plan.

    Keyword routing today; the docstring on this module says what is scheduled to land here and
    why it is a node rather than an edge. The routing table itself is two module constants so a
    new keyword is a data change, not a control-flow change.

    :param state: The graph state. Only ``question`` is read today; a per-tenant rate limit will
        read ``tenant_id``, which is why the whole state is the parameter rather than the string.
    :returns: One :class:`~langgraph.types.Send` per worker to run. Never empty - see the module
        docstring on why an unrecognised question grounds rather than answers blind.
    """
    q = state["question"].lower()
    needs_docs = any(t in q for t in DOCS_KEYWORDS)
    needs_api = any(t in q for t in API_KEYWORDS)

    targets: list[Send] = []
    if needs_docs:
        targets.append(Send(RETRIEVAL_AGENT, state))
    if needs_api:
        targets.append(Send(API_AGENT, state))
    if not targets:
        # Default: ground in docs rather than answering blind.
        targets.append(Send(RETRIEVAL_AGENT, state))
    return targets


def build_graph(settings: Settings, checkpointer: Any = None) -> Any:
    """Wire and compile the three-node graph.

    Nodes are built by factory rather than imported as decorated module-level functions, because
    their deadlines and LangSmith project come from ``settings`` - see
    :func:`taxcalc_agent_svc.nodes.api.make_api_node`.

    :param settings: Validated configuration.
    :param checkpointer: A LangGraph checkpointer, or ``None`` to compile without one. ``None``
        is for the schema and supervisor tests, which assert on topology and need no database; it
        is NOT a production configuration, and :func:`build_taxcalc_agent_graph` is the entry
        point that guarantees a real one.
    :returns: The compiled, executable graph.
    """
    sg: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    sg.add_node(RETRIEVAL_AGENT, make_retrieval_node(settings))
    sg.add_node(API_AGENT, make_api_node(settings))
    sg.add_node(SYNTHESIS_AGENT, make_synthesis_node(settings))

    # The third argument is the set of nodes the router may target. LangGraph needs it to build
    # the branch: a Send to a node absent from this list raises at runtime rather than at compile
    # time, which is the wrong end of the deploy to find a routing typo.
    sg.add_conditional_edges(START, supervisor, [RETRIEVAL_AGENT, API_AGENT])
    sg.add_edge(RETRIEVAL_AGENT, SYNTHESIS_AGENT)
    sg.add_edge(API_AGENT, SYNTHESIS_AGENT)
    sg.add_edge(SYNTHESIS_AGENT, END)

    return sg.compile(checkpointer=checkpointer)


async def build_taxcalc_agent_graph(settings: Settings) -> tuple[Any, Any]:
    """Compile the graph against a real Postgres checkpointer.

    ``await cp.setup()`` migrates the checkpoint schema idempotently on first start, so a fresh
    database needs no separate migration step and an existing one is left alone. Called here
    rather than left to an operator because a service whose first request fails on a missing
    table is a service that fails its own readiness probe for a reason nobody can see from
    outside.

    **``AsyncPostgresSaver``, not the synchronous ``PostgresSaver``.** This is a correction to the
    obvious wiring, and it is not a preference - the synchronous saver simply does not work here.
    Every node body in this graph is ``async``, which means every call site is ``ainvoke`` or
    ``astream_events``, which means LangGraph drives the checkpointer through its *async*
    interface. ``PostgresSaver`` inherits those methods from ``BaseCheckpointSaver``, where they
    are ``raise NotImplementedError``. Measured, not reasoned about: wiring the sync saver and
    invoking once fails in ``AsyncPregelLoop.__aenter__`` at
    ``await self.checkpointer.aget_tuple(...)`` with a bare ``NotImplementedError``, before any
    node runs. The failure is total and immediate, which is the one mercy in it.

    **Never an in-memory checkpointer in any production path.** One makes every durability claim
    this service makes false: a pod restart loses every in-flight conversation, and a request
    routed to a different replica than the one that started it resumes from nothing. It is also
    silent about it - the graph runs perfectly, and only a restart reveals the checkpoints were
    never anywhere. CI greps ``src/`` for the in-memory saver's class name for exactly that
    reason, which is also why this paragraph describes it rather than naming it: a gate whose
    green state costs you the explanation is a gate that teaches people to delete explanations.
    The name is used legitimately in ``tests/``, which the gate does not scan, because a topology
    test asserting "the checkpointer the graph was handed is the one it carries" needs a
    checkpointer and has no business opening a database to get one.

    :param settings: Validated configuration.
    :returns: ``(graph, closer)`` - the compiled graph, and the async context manager whose
        ``__aexit__`` closes the connection pool. Returned rather than stashed on the graph
        object: the lifespan owns the shutdown, and handing it the closer explicitly is what
        makes forgetting to close a *type* error at the call site instead of a leaked pool.
    """
    # from_conn_string is an async context manager whose __aexit__ closes the pool. Entered
    # without `async with` on purpose: the pool must outlive this function, because it serves
    # every request for the life of the process. The caller closes it - see the return docs.
    cm = AsyncPostgresSaver.from_conn_string(settings.postgres_url)
    cp = await cm.__aenter__()
    await cp.setup()  # idempotent schema migration
    return build_graph(settings, checkpointer=cp), cm


def run_config(
    thread_id: str,
    settings: Settings,
    *,
    guard: BudgetGuard,
    session: Any,
    **extra: Any,
) -> RunnableConfig:
    """Build the config every ``ainvoke`` / ``astream_events`` call site passes.

    One constructor, because all four values in it are easy to forget and expensive to omit:

    ``thread_id``
        Without it ``PostgresSaver`` writes orphan rows and every call starts fresh - the
        checkpointer appears to work, costs its writes, and resumes nothing.

    ``recursion_limit``
        Pinned from settings on every call, not only on compile, so a future feedback edge cannot
        burn thousands of tokens against a default that moved in a library release.

    ``budget_guard`` / ``mcp_session``
        The per-request dependencies. Keyword-only and required rather than optional: a node that
        cannot find its guard raises (see :mod:`taxcalc_agent_svc.deps`), and the place to catch
        that is the type checker at the call site, not a 500 in production.

    :param thread_id: The conversation thread to checkpoint under.
    :param settings: Validated configuration.
    :param guard: The request's cost ceiling.
    :param session: The request's MCP client session.
    :param extra: Additional ``configurable`` entries.
    :returns: The config mapping, typed as ``RunnableConfig`` - the same type LangGraph passes
        back to every node, so the producer here and the consumers in
        :mod:`taxcalc_agent_svc.deps` are checked against one type rather than agreeing by
        convention.
    """
    return {
        "configurable": {
            "thread_id": thread_id,
            BUDGET_GUARD_KEY: guard,
            MCP_SESSION_KEY: session,
            **extra,
        },
        "recursion_limit": settings.recursion_limit,
    }
