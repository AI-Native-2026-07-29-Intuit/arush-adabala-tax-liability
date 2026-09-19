# taxcalc-agent-svc/src/taxcalc_agent_svc/sse.py
"""Bridge LangGraph's ``astream_events(version="v2")`` to the W4 D4 useChat data-stream protocol.

The React app built on W4 D4 already speaks the Vercel AI SDK's data-stream wire format, so this
module's whole job is translation - not invention. Three channels are emitted:

``0:<json string>``
    A text delta. The client appends these to the streaming message, which is what makes tokens
    appear as they are generated rather than after the whole answer exists.

``2:<json object>``
    The typed :class:`~taxcalc_agent_svc.nodes.synthesis.FinalAnswer`, emitted once, when the
    synthesis node closes. This is the channel that carries citations and confidence - the fields
    a text stream cannot express - and it is why the client can render sources at all.

``3:<json object>``
    An error. **``GraphRecursionError`` and ``BudgetExceeded`` are emitted distinctly**, and that
    distinction is the point of having this channel rather than a generic 500. They mean opposite
    things to whoever is looking at the client: a recursion breach is a *bug* - the graph is
    looping and the same request will loop again - while a budget breach is a *limit* - the
    request was legitimate and progressing, and the operator may simply raise the ceiling.
    Collapsing both into "something went wrong" costs the reader the one fact that decides what
    to do next.

**The stream is decorated with one ``@traceable(name="chat_request")`` root.** Every node's span
nests underneath it, so LangSmith shows one root run per HTTP request with three named children
rather than three orphan runs nobody can correlate. The trace id is returned on the
``X-LangSmith-Trace-Id`` response header so the client can deep-link "view trace" from a failed
answer - which is what turns a user's bug report into a URL instead of a timestamp.

**Errors are emitted into the stream, not raised out of it.** By the time the first event has
been yielded the response status is already 200 and the headers are already on the wire; raising
after that point truncates the body and the client sees a connection drop with no explanation. An
error event is the only way to tell the client *why* a stream ended. The FastAPI handler still
maps a pre-stream failure to a real status code - see :mod:`taxcalc_agent_svc.app`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Final

from langgraph.errors import GraphRecursionError
from langsmith import get_current_run_tree, traceable

from taxcalc_agent_svc.budgets import BudgetExceeded
from taxcalc_agent_svc.graph import SYNTHESIS_AGENT

#: Data-stream channel prefixes. Named rather than inlined so the three emit sites and the tests
#: that assert on them cannot drift apart.
TEXT_CHANNEL: Final[str] = "0"
DATA_CHANNEL: Final[str] = "2"
ERROR_CHANNEL: Final[str] = "3"

#: Response header carrying the root run id, for the client's "view trace" deep link.
TRACE_HEADER: Final[str] = "X-LangSmith-Trace-Id"

#: Error codes emitted on the ``3:`` channel. Stable strings: the React client branches on them,
#: so they are API surface rather than log text.
ERROR_RECURSION: Final[str] = "recursion_limit"
ERROR_BUDGET: Final[str] = "budget_exceeded"
ERROR_INTERNAL: Final[str] = "internal"


def frame(channel: str, payload: Any) -> bytes:
    """Encode one data-stream frame.

    :param channel: The channel prefix - ``0``, ``2`` or ``3``.
    :param payload: The JSON-serialisable payload.
    :returns: The encoded frame, newline-terminated as the protocol requires.
    """
    return f"{channel}:{json.dumps(payload)}\n".encode()


def _final_answer_payload(output: Any) -> dict[str, Any] | None:
    """Extract the typed answer from a synthesis node's output, if it produced one.

    The node stores ``answer`` as a JSON *string* (see
    :mod:`taxcalc_agent_svc.nodes.synthesis` on why it is not a live model), so it is parsed back
    into an object here - the client expects a structured ``finalAnswer``, not a string holding
    JSON, and shipping the string would push the parse into the browser where a failure is much
    harder to see.

    :param output: The node's output mapping.
    :returns: The parsed answer, or ``None`` when this event carried no answer.
    """
    if not isinstance(output, dict):
        return None
    raw = output.get("answer")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        # A malformed answer is degraded into a text-only payload rather than killing the stream.
        # Instructor validates this object on the way out, so reaching here means something
        # downstream of that corrupted it - which is worth surviving, and worth not hiding.
        return {"text": str(raw), "citations": [], "confidence": 0.0}
    return parsed if isinstance(parsed, dict) else None


@traceable(name="chat_request")
async def event_stream(
    graph: Any,
    question: str,
    tenant_id: str,
    thread_id: str,
    config: dict[str, Any],
) -> AsyncIterator[bytes]:
    """Stream one request's events in the useChat data-stream format.

    :param graph: The compiled graph.
    :param question: The user's question.
    :param tenant_id: The requesting tenant.
    :param thread_id: The checkpoint thread.
    :param config: The run config from :func:`taxcalc_agent_svc.graph.run_config`, carrying the
        ``thread_id``, the pinned ``recursion_limit`` and the per-request dependencies.
    :yields: Encoded data-stream frames.
    """
    inputs = {"question": question, "tenant_id": tenant_id, "thread_id": thread_id}
    try:
        async for ev in graph.astream_events(inputs, config, version="v2"):
            kind = ev.get("event")
            if kind == "on_chat_model_stream":
                chunk = ev["data"].get("chunk")
                delta = getattr(chunk, "content", None)
                if delta:
                    yield frame(TEXT_CHANNEL, delta)
            elif kind == "on_chain_end" and ev.get("name") == SYNTHESIS_AGENT:
                payload = _final_answer_payload(ev["data"].get("output"))
                if payload is not None:
                    yield frame(DATA_CHANNEL, {"finalAnswer": payload})
    except GraphRecursionError as exc:
        # A BUG: the graph looped. The same request will loop again; raising the ceiling is the
        # wrong fix. Distinct from the budget case immediately below on purpose.
        yield frame(ERROR_CHANNEL, {"error": ERROR_RECURSION, "detail": str(exc)})
    except BudgetExceeded as exc:
        # A LIMIT: the run was progressing and legitimate, and simply cost more than its ceiling.
        # An operator may reasonably raise the ceiling; nothing here needs debugging.
        yield frame(ERROR_CHANNEL, {"error": ERROR_BUDGET, "detail": str(exc)})


def current_trace_id() -> str:
    """Return the current root run id, for the ``X-LangSmith-Trace-Id`` header.

    Empty when tracing is disabled, which is the honest answer - a fabricated id would produce a
    "view trace" link that 404s, which is worse than no link because it costs the reader a click
    and a moment of doubt before they conclude the tracing is broken.

    :returns: The trace id, or an empty string.
    """
    run = get_current_run_tree()
    return "" if run is None else str(run.trace_id)
