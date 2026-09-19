# taxcalc-agent-svc/src/taxcalc_agent_svc/nodes/api.py
"""API agent: MCP catalogue discovery + a bounded Anthropic tool-use loop.

**The tool catalogue is discovered, never hard-coded.** ``session.list_tools()`` is what this
node knows about tools; the four names the W7 D4 server happens to publish today appear nowhere
in this module. Hard-coding them would mean a fifth tool shipped by the MCP server is invisible
to the agent until someone edits this file, and a renamed tool fails at call time with a
"tool not found" the model cannot act on. Discovery makes the MCP server the single source of
truth for its own surface, which is the entire point of putting a protocol between them.

**The loop is bounded at five iterations, and that bound is not the graph's recursion limit.**
``recursion_limit`` bounds super-steps *between* nodes; this bounds the model's tool-use turns
*inside* one node. A model that keeps asking for one more tool call without ever producing a
final answer never returns control to the graph, so the graph's limit would never be consulted.
Five covers the realistic worst case here - look up an order, discover it needs a policy,
retrieve it, refund, confirm - and is small enough that a confused model costs five calls rather
than a budget.

**Tenancy and idempotency are injected as tool ARGUMENTS, schema-driven - not as headers.**
This is where this module departs from the reference sketch, and the departure is forced twice
over. ``ClientSession.call_tool`` in mcp 1.x is
``call_tool(name, arguments, read_timeout_seconds, progress_callback, *, meta)`` - there is no
``headers`` parameter, so ``session.call_tool(name, args, headers={...})`` raises ``TypeError``
before a byte reaches the server. And the W7 D4 server hardens every tool's argument model to
reject unknown keys, so a blind ``args["tenant_id"] = ...`` would be rejected outright on any
tool that did not declare it.

Both facts point at the same answer: read the tool's own published ``inputSchema`` and inject
only what it declares. Introspecting the live D4 catalogue, all four tools declare ``tenant_id``
and exactly one - ``orders.create_refund`` - declares ``idempotency_key``, which is precisely the
write tool that needs it. So the schema *is* the instruction, and this node needs no table of
which tool takes what.

**Tenancy is injected rather than trusted, and that is a security boundary.** The model proposes
tool arguments, and a model that has read a document mentioning ``tenant-b`` can propose
``tenant_id="tenant-b"``. Overwriting it with the request's own tenant - unconditionally, after
the model has spoken - means a prompt-injected or merely confused model cannot reach another
tenant's data. The MCP server enforces the same boundary again upstream; defence in depth is the
point, not redundancy.

**Every write tool call carries a deterministic UUID5 idempotency key.** The key is derived from
``(thread_id, tool_name, args_hash)``, so the *same* logical call always produces the *same*
key. That is what makes the three ways one refund becomes two all collapse to one:

* the Anthropic loop re-issuing a call whose result it never saw,
* the graph resuming from a Postgres checkpoint after a pod restart and replaying this node,
* a deadline cancelling a call mid-flight and the retry that follows.

A random UUID4 per call would satisfy the *schema* and defeat the *purpose*: the MCP server
de-duplicates on the key's value, so a fresh key per attempt makes every retry a new refund. The
namespace is a module constant for the same reason - re-rolling it per process would give the
same logical call a different key after a restart, which is the exact case the checkpointer
exists to survive.

``json.dumps(args, sort_keys=True)`` and not ``str(args)``: two dicts with the same contents in a
different insertion order must hash to the same key, and ``str()`` on a dict is insertion-ordered.
The hash is taken over the args the model proposed *before* the key itself is inserted, because a
key derived from a payload containing that key cannot be recomputed by anyone checking it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Final, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolParam, ToolResultBlockParam
from langchain_core.runnables import RunnableConfig
from langsmith import traceable

from taxcalc_agent_svc.deps import AgentNode, budget_guard, mcp_session
from taxcalc_agent_svc.nodes._deadline import deadline
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.state import AgentState

#: Fixed namespace for the UUID5 idempotency keys. A CONSTANT, never ``uuid.uuid4()`` at import:
#: a namespace that changed per process would give the same logical call a different key after a
#: pod restart, which is the exact scenario the deterministic key exists to survive.
IDEMPOTENCY_NS: Final[uuid.UUID] = uuid.UUID("12345678-1234-5678-1234-567812345678")

#: Turns the model gets to call tools inside one visit to this node. See the module docstring for
#: why this is a different bound from the graph's ``recursion_limit``.
MAX_TOOL_ITERATIONS: Final[int] = 5

#: Ceiling on each tool-use completion. Small deliberately: this node's replies are tool-call
#: blocks and short reasoning, not prose. Synthesis is where the answer is written.
MAX_TOKENS: Final[int] = 1024

#: Argument names this node injects when - and only when - a tool's published schema declares
#: them. Named constants rather than string literals so the injection sites and the tests that
#: assert on them cannot drift apart.
TENANT_ARG: Final[str] = "tenant_id"
IDEMPOTENCY_ARG: Final[str] = "idempotency_key"



def idempotency_key(thread_id: str, tool: str, args: dict[str, Any]) -> str:
    """Derive the deterministic idempotency key for one tool call.

    :param thread_id: The checkpoint thread the call belongs to. Included so two different
        conversations issuing an identical refund are two refunds, not one - the key must
        de-duplicate *retries*, never *distinct requests*.
    :param tool: The tool name.
    :param args: The arguments the model proposed, before injection. Serialised with
        ``sort_keys=True`` so key order cannot change the hash.
    :returns: A UUID5 string, stable across retries, restarts and checkpoint replays.
    """
    payload = f"{thread_id}|{tool}|{json.dumps(args, sort_keys=True, default=str)}"
    return str(uuid.uuid5(IDEMPOTENCY_NS, payload))


def tools_for_claude(catalogue: Any) -> list[ToolParam]:
    """Reshape an MCP tool catalogue into Anthropic's tool-definition format.

    The two schemas carry the same information under different key names - MCP publishes
    ``inputSchema``, Anthropic expects ``input_schema`` - so this is a rename, not a translation.
    Its own function rather than inlined because it is the seam a test drives with a fake
    catalogue and no server.

    :param catalogue: The result of ``session.list_tools()``.
    :returns: Tool definitions ``messages.create`` accepts.
    """
    return [
        ToolParam(name=t.name, description=t.description, input_schema=t.inputSchema)
        for t in catalogue.tools
    ]


def declared_properties(tools: list[ToolParam], name: str) -> set[str]:
    """Return the argument names one tool's published schema declares.

    :param tools: The reshaped catalogue from :func:`tools_for_claude`.
    :param name: The tool to look up.
    :returns: Its declared property names; an empty set for a tool not in the catalogue, so a
        model hallucinating a tool name injects nothing rather than raising here - the call
        itself will fail upstream, with the server's own error, which is the more legible place.
    """
    for tool in tools:
        if tool["name"] == name:
            props = tool["input_schema"].get("properties") or {}
            return set(props) if isinstance(props, dict) else set()
    return set()


def inject_context(
    args: dict[str, Any], declared: set[str], *, tenant_id: str, thread_id: str, tool: str
) -> dict[str, Any]:
    """Overwrite tenancy and add an idempotency key, but only where the schema declares them.

    Returns a new mapping rather than mutating in place, so the caller keeps the model's original
    proposal - which is what the idempotency hash is taken over, and what a trace shows when
    someone asks what the model actually asked for.

    :param args: The arguments the model proposed.
    :param declared: The tool's declared property names, from :func:`declared_properties`.
    :param tenant_id: The request's tenant. Overwrites whatever the model proposed; see the
        module docstring on why this is a boundary and not a default.
    :param thread_id: The checkpoint thread, for the idempotency key.
    :param tool: The tool name, for the idempotency key.
    :returns: The arguments to send.
    """
    sent = dict(args)
    if TENANT_ARG in declared:
        sent[TENANT_ARG] = tenant_id
    if IDEMPOTENCY_ARG in declared:
        # Hashed over the model's proposal, NOT over `sent` - a key derived from a payload that
        # already contains the key cannot be recomputed by anyone verifying it.
        sent[IDEMPOTENCY_ARG] = idempotency_key(thread_id, tool, args)
    return sent


async def _api(
    state: AgentState, config: RunnableConfig | None, settings: Settings
) -> dict[str, Any]:
    """Run the bounded tool-use loop against the discovered MCP catalogue.

    Undecorated on purpose. Both ``@traceable`` and ``@deadline`` are applied in
    :func:`make_api_node`, because their *relative order* is load-bearing (see
    :mod:`taxcalc_agent_svc.nodes._deadline`) and splitting them across two files would put half
    of a single decision out of sight of the other half.

    The budget is checked *before* each completion and recorded *after* it, so a loop that is
    progressing but expensive terminates on the dollar ceiling rather than on the turn count.
    :class:`~taxcalc_agent_svc.budgets.BudgetExceeded` is deliberately **not** caught here: it
    propagates through the graph to the SSE bridge, which emits it as a distinct error event. A
    node that swallowed it would turn a budget breach into a silently truncated answer.

    :param state: The graph state.
    :param config: The LangGraph config. The MCP session and budget guard are read off its
        ``configurable`` mapping - see :mod:`taxcalc_agent_svc.deps` for why they are there and
        not in the state.
    :param settings: Validated configuration.
    :returns: A partial state carrying ``tool_results``, this node's spend, and its name appended
        to ``visited_nodes``.
    :raises BudgetExceeded: when the per-request ceiling is reached mid-loop.
    """
    session = mcp_session(config)
    guard = budget_guard(config)

    # Per-agent header so the W3 D1 llm-proxy emits `api_cost_per_request` as its own CloudWatch
    # SLI. Three agents sharing one un-tagged client would report a single undifferentiated
    # number, and the question an on-call actually asks - which agent got expensive - would have
    # no answer in the metrics.
    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value() or None,
        default_headers={"X-Agent": "api"},
    )

    catalogue = await session.list_tools()
    tools = tools_for_claude(catalogue)

    messages: list[MessageParam] = [{"role": "user", "content": state["question"]}]
    tool_results: dict[str, Any] = {}
    spent_before = guard.spent_usd_e5

    for _ in range(MAX_TOOL_ITERATIONS):
        guard.check_or_raise()
        resp = await client.messages.create(
            model=settings.model,
            max_tokens=MAX_TOKENS,
            tools=tools,
            messages=messages,
        )
        guard.record_call(resp)

        if resp.stop_reason != "tool_use":
            break

        # The assistant turn is appended BEFORE its tool results, because Anthropic's protocol
        # requires a tool_use block and its tool_result to be adjacent turns. Out of order, the
        # next request 400s on an unmatched tool_use id.
        messages.append({"role": "assistant", "content": resp.content})
        results_turn: list[ToolResultBlockParam] = []

        for block in resp.content:
            if block.type != "tool_use":
                continue
            proposed = dict(block.input) if isinstance(block.input, dict) else {}
            sent = inject_context(
                proposed,
                declared_properties(tools, block.name),
                tenant_id=state["tenant_id"],
                thread_id=state["thread_id"],
                tool=block.name,
            )
            # `meta` is mcp 1.x's per-call `_meta` field - the protocol-level equivalent of the
            # headers the reference sketch reached for. It carries the same two values for
            # server-side logging and correlation; the ARGUMENTS above are what actually enforce
            # them, because that is what the server validates.
            result = await session.call_tool(
                block.name,
                sent,
                meta={"tenant_id": state["tenant_id"], "thread_id": state["thread_id"]},
            )
            tool_results[block.name] = result.content
            results_turn.append(
                ToolResultBlockParam(
                    type="tool_result", tool_use_id=block.id, content=str(result.content)
                )
            )

        messages.append({"role": "user", "content": results_turn})

    return {
        "tool_results": tool_results,
        "cost_usd_e5": guard.spent_usd_e5 - spent_before,
        "visited_nodes": ["api_agent"],
    }


def make_api_node(settings: Settings) -> AgentNode:
    """Build the deadline-bounded api node.

    A factory rather than a module-level decorated function because the deadline and the
    LangSmith project are *configuration*: ``@deadline(seconds=5.0)`` at module scope would bake
    the p99 budget into the source, and tuning it would be a code change rather than an env var.

    The sentinel names this node's own output channel, so a timeout contributes an empty
    ``tool_results`` and synthesis still runs on whatever retrieval found.

    :param settings: Validated configuration supplying the deadline.
    :returns: The node callable LangGraph registers as ``api_agent``.
    """

    # @traceable OUTERMOST, @deadline beneath it - i.e. @deadline applied FIRST. Measured, not
    # assumed: with the decorators the other way round the timeout handler's
    # get_current_run_tree() returns the ROOT `chat_request` run, so `deadline_exceeded=True`
    # marks the whole request instead of the node that missed its budget. See
    # :mod:`taxcalc_agent_svc.nodes._deadline` for the measurement.
    @traceable(name="api_agent", project_name=settings.langsmith_project)
    @deadline(seconds=settings.deadline_api_s, sentinel={"tool_results": {}})
    async def api_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Call the MCP tools under this node's deadline.

        :param state: The graph state.
        :param config: The LangGraph config carrying the request's session and budget guard.
        :returns: A partial state carrying ``tool_results``.
        """
        return await _api(state, config, settings)

    # cast, with a reason. `@traceable` returns a wrapper declared as `(*args, **kwargs)`, so the
    # named-parameter shape `AgentNode` (and LangGraph's own `_NodeWithConfig`) requires is erased
    # at the type level even though `functools.wraps` preserves it at runtime. The cast restores
    # the fact the decorator loses; the alternative is annotating every `add_node` call `Any`,
    # which turns off checking for the one argument most worth checking.
    return cast(AgentNode, api_node)
