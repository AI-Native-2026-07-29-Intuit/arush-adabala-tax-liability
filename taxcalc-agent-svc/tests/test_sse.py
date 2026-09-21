# taxcalc-agent-svc/tests/test_sse.py
"""The SSE bridge emits the three useChat channels, and the two errors stay distinguishable.

The channel prefixes are wire format: the W4 D4 React client parses ``0:``, ``2:`` and ``3:`` and
branches on them. A change here is a breaking API change for a client that lives in another
project and will not be recompiled against this one, which is why the frames are asserted on
byte-for-byte rather than through a helper that could drift alongside the code it checks.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langgraph.errors import GraphRecursionError
from langsmith import Client, get_current_run_tree, traceable
from langsmith.run_helpers import tracing_context

from taxcalc_agent_svc.budgets import BudgetExceeded
from taxcalc_agent_svc.sse import (
    DATA_CHANNEL,
    DEFAULT_PROJECT,
    ERROR_BUDGET,
    ERROR_CHANNEL,
    ERROR_INTERNAL,
    ERROR_RECURSION,
    TEXT_CHANNEL,
    event_stream,
    frame,
    new_trace_id,
)

ANSWER_JSON = json.dumps(
    {
        "text": "the answer",
        "citations": [{"doc_id": "doc-1", "quote": "a supporting line"}],
        "confidence": 0.88,
    }
)


class Chunk:
    """A streamed model chunk carrying a text delta."""

    def __init__(self, content: str) -> None:
        """Construct a chunk.

        :param content: The delta text.
        """
        self.content = content


class FakeGraph:
    """A graph whose ``astream_events`` replays a scripted event list.

    Scripted rather than driven through a real graph because the bridge's job is *translation*,
    and translating a fixed input into a fixed output is exactly what can be asserted without a
    model, a database or a tool server.
    """

    def __init__(
        self, events: list[dict[str, Any]], raises: BaseException | None = None
    ) -> None:
        """Construct a fake graph.

        :param events: The events to replay.
        :param raises: An exception to raise after replaying them. Typed ``BaseException`` so a
            test can inject ``CancelledError``, which the bridge must NOT convert into a frame.
        """
        self._events = events
        self._raises = raises

    def astream_events(
        self, _inputs: dict[str, Any], _config: dict[str, Any], version: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Replay the scripted events.

        :param version: Asserted to be ``v2`` - the bridge is written against that schema, and
            a silent downgrade would change the event names it filters on.
        :returns: The async iterator of events.
        """
        assert version == "v2"

        async def gen() -> AsyncIterator[dict[str, Any]]:
            for ev in self._events:
                yield ev
            if self._raises is not None:
                raise self._raises

        return gen()


async def _collect(graph: FakeGraph) -> list[bytes]:
    """Drain the bridge into a list of frames.

    :param graph: The fake graph to stream from.
    :returns: The emitted frames.
    """
    return [f async for f in event_stream(graph, "q", "tenant-a", "t-1", {})]


def test_frame_encoding_matches_the_data_stream_wire_format() -> None:
    """``<channel>:<json>\\n`` - the format the W4 D4 client parses."""
    assert frame(TEXT_CHANNEL, "hi") == b'0:"hi"\n'
    assert frame(DATA_CHANNEL, {"a": 1}) == b'2:{"a": 1}\n'


async def test_model_deltas_are_emitted_on_channel_0() -> None:
    """Streamed tokens reach the client as they are generated."""
    graph = FakeGraph(
        [
            {"event": "on_chat_model_stream", "data": {"chunk": Chunk("Hel")}},
            {"event": "on_chat_model_stream", "data": {"chunk": Chunk("lo")}},
        ]
    )
    assert await _collect(graph) == [b'0:"Hel"\n', b'0:"lo"\n']


async def test_an_empty_delta_is_not_emitted() -> None:
    """Empty chunks are dropped rather than sent as empty frames.

    A stream of ``0:""`` frames costs bandwidth and makes the client re-render for no change.
    """
    graph = FakeGraph([{"event": "on_chat_model_stream", "data": {"chunk": Chunk("")}}])
    assert await _collect(graph) == []


async def test_the_typed_answer_is_emitted_once_on_channel_2() -> None:
    """The synthesis node's close carries the parsed FinalAnswer, citations included.

    Parsed, not passed through as a string: the node stores ``answer`` as JSON text so it
    survives the checkpointer, and shipping that string would push the parse into the browser
    where a failure is far harder to see.
    """
    graph = FakeGraph(
        [
            {
                "event": "on_chain_end",
                "name": "synthesis_agent",
                "data": {"output": {"answer": ANSWER_JSON}},
            }
        ]
    )
    frames = await _collect(graph)
    assert len(frames) == 1
    assert frames[0].startswith(b"2:")
    payload = json.loads(frames[0][2:])
    assert payload["finalAnswer"]["confidence"] == 0.88
    assert payload["finalAnswer"]["citations"][0]["doc_id"] == "doc-1"


async def test_another_nodes_close_does_not_emit_a_final_answer() -> None:
    """Only ``synthesis_agent`` closing produces the ``2:`` frame.

    Without the name filter, the retrieval and api nodes closing would each emit a frame and the
    client would render three answers for one question.
    """
    graph = FakeGraph(
        [{"event": "on_chain_end", "name": "retrieval_agent", "data": {"output": {"docs": []}}}]
    )
    assert await _collect(graph) == []


async def test_a_recursion_breach_is_its_own_error_code() -> None:
    """``GraphRecursionError`` -> ``3:{"error": "recursion_limit"}``.

    A BUG: the graph looped and will loop again. Distinct from the budget case below because the
    two demand opposite responses from whoever reads them.
    """
    graph = FakeGraph([], raises=GraphRecursionError("hit 25"))
    frames = await _collect(graph)
    assert frames[0].startswith(b"3:")
    assert json.loads(frames[0][2:])["error"] == ERROR_RECURSION


async def test_a_budget_breach_is_its_own_error_code() -> None:
    """``BudgetExceeded`` -> ``3:{"error": "budget_exceeded"}``.

    A LIMIT, not a bug: the run was legitimate and progressing, and an operator may simply raise
    the ceiling. Collapsing this into the recursion code would cost the reader the one fact that
    decides what to do next.
    """
    graph = FakeGraph([], raises=BudgetExceeded("spent=25000 >= ceiling=25000"))
    frames = await _collect(graph)
    assert json.loads(frames[0][2:])["error"] == ERROR_BUDGET


async def test_the_two_error_codes_are_not_the_same_string() -> None:
    """The distinction is the point; a refactor that merged them would be silent."""
    assert ERROR_RECURSION != ERROR_BUDGET


async def test_deltas_already_emitted_survive_a_later_error() -> None:
    """An error ends the stream; it does not retract what the client already rendered.

    The error frame is emitted INTO the stream rather than raised out of it, because by this
    point the 200 and the headers are already on the wire - raising would truncate the body and
    the client would see a bare connection drop with no reason attached.
    """
    graph = FakeGraph(
        [{"event": "on_chat_model_stream", "data": {"chunk": Chunk("partial")}}],
        raises=BudgetExceeded("over"),
    )
    frames = await _collect(graph)
    assert frames[0] == b'0:"partial"\n'
    assert frames[1].startswith(b"3:")


async def test_a_corrupt_answer_degrades_rather_than_killing_the_stream() -> None:
    """Unparseable answer text becomes a zero-confidence payload, not an exception.

    Instructor validates this object on the way out, so reaching here means something downstream
    corrupted it - worth surviving, and worth not hiding behind a silent drop.
    """
    graph = FakeGraph(
        [
            {
                "event": "on_chain_end",
                "name": "synthesis_agent",
                "data": {"output": {"answer": "not json at all"}},
            }
        ]
    )
    payload = json.loads((await _collect(graph))[0][2:])
    assert payload["finalAnswer"]["confidence"] == 0.0
    assert payload["finalAnswer"]["text"] == "not json at all"


@pytest.mark.parametrize("channel", [TEXT_CHANNEL, DATA_CHANNEL, ERROR_CHANNEL])
def test_the_channel_prefixes_are_the_protocol_values(channel: str) -> None:
    """0 / 2 / 3, as the Vercel AI SDK data-stream protocol defines them."""
    assert channel in {"0", "2", "3"}


# ------------------------------------------------------------------ the third error code


async def test_an_unexpected_failure_is_its_own_error_code() -> None:
    """Anything that is neither a recursion breach nor a budget breach becomes ``3:`` internal.

    Before this existed the stream caught only the two known errors, so a third kind of failure
    escaped the async generator after the 200 and the headers had already gone out - which
    truncates the body and shows the client a bare connection drop. An error the client cannot
    act on is still worth telling it about; being unable to act is different from being unable
    to tell that anything happened.
    """
    graph = FakeGraph([], raises=RuntimeError("the checkpointer connection died"))
    frames = await _collect(graph)
    payload = json.loads(frames[0][2:])
    assert payload["error"] == ERROR_INTERNAL
    assert "checkpointer connection died" in payload["detail"]


async def test_deltas_survive_an_unexpected_failure_too() -> None:
    """The generic case gets the same treatment as the two named ones: emit, do not raise."""
    graph = FakeGraph(
        [{"event": "on_chat_model_stream", "data": {"chunk": Chunk("partial")}}],
        raises=RuntimeError("boom"),
    )
    frames = await _collect(graph)
    assert frames[0] == b'0:"partial"\n'
    assert json.loads(frames[1][2:])["error"] == ERROR_INTERNAL


async def test_a_cancelled_client_is_not_reported_as_an_error() -> None:
    """A client that hung up is not a failure to narrate to the client that hung up.

    ``except Exception`` rather than ``except BaseException`` is what keeps this true:
    ``CancelledError`` derives from ``BaseException``, so it passes through and the task ends as
    cancelled rather than being swallowed into a frame nobody will read.
    """
    import asyncio

    graph = FakeGraph([], raises=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await _collect(graph)


def test_the_three_error_codes_are_three_distinct_strings() -> None:
    """The client branches on these; two that collide would silently merge two failure modes."""
    assert len({ERROR_RECURSION, ERROR_BUDGET, ERROR_INTERNAL}) == 3


# ------------------------------------------------------------------ the trace header


def test_no_trace_id_is_minted_when_tracing_is_disabled() -> None:
    """An empty header, not a fabricated id.

    A "view trace" link built from an id no run ever had is worse than no link: it costs the
    reader a click and a moment of doubt before they work out that tracing was simply off.
    """
    with tracing_context(enabled=False):
        assert new_trace_id() == ""


def test_a_trace_id_is_minted_when_tracing_is_enabled(offline_langsmith_client: Client) -> None:
    """A real, parseable run id - available BEFORE the stream starts, which is the whole point.

    The header has to be written before the first frame is yielded, and the root run does not
    exist until the generator is first iterated. Minting the id here and handing it to
    ``@traceable`` is what lets the header and the run agree.
    """
    with tracing_context(enabled="local", client=offline_langsmith_client):
        trace_id = new_trace_id()
    assert uuid.UUID(trace_id).version == 4


async def test_the_minted_id_becomes_the_root_runs_id(
    offline_langsmith_client: Client,
) -> None:
    """The id in the header is the id of the run, not merely a correlation value.

    Asserted against a real ``RunTree``: the stream reports the run it is executing inside, and
    that run's id must be the one the caller chose. A root run's id is its trace id, so this is
    what makes the deep link resolve to this request rather than to nothing.
    """
    seen: list[str] = []

    @traceable(name="chat_request", project_name="taxcalc-agent-svc-dev")
    async def probe() -> None:
        """Report the run tree the decorator built."""
        run = get_current_run_tree()
        assert run is not None
        seen.append(str(run.id))

    with tracing_context(enabled="local", client=offline_langsmith_client):
        chosen = new_trace_id()
        await probe(langsmith_extra={"run_id": chosen})

    assert seen == [chosen]


async def test_the_root_run_lands_in_the_named_project(
    offline_langsmith_client: Client,
) -> None:
    """One project for the root and its three children, because a trace is filed by its ROOT.

    The three node decorators name ``settings.langsmith_project``; the root named none at all,
    and children inherit their parent's project rather than applying their own. The whole trace
    therefore landed wherever the bare ``LANGSMITH_PROJECT`` pointed - which this service never
    sets - while every query ran against ``taxcalc-agent-svc-dev``.

    Read off the finished ``RunTree`` rather than off the decorator's arguments: ``session_name``
    is the project the run would actually be written to, which is the fact that was wrong.
    """
    graph = FakeGraph([{"event": "on_chat_model_stream", "data": {"chunk": Chunk("hi")}}])
    runs: list[Any] = []

    with tracing_context(enabled="local", client=offline_langsmith_client):
        async for _ in event_stream(
            graph, "q", "tenant-a", "t-1", {}, langsmith_extra={"on_end": runs.append}
        ):
            pass

    assert [r.name for r in runs] == ["chat_request"]
    assert runs[0].session_name == DEFAULT_PROJECT == "taxcalc-agent-svc-dev"


async def test_the_handler_can_move_the_whole_trace_to_a_configured_project(
    offline_langsmith_client: Client,
) -> None:
    """``settings.langsmith_project`` overrides the module default, on the root.

    This is how :mod:`taxcalc_agent_svc.app` keeps a deployment pointed at one project: it passes
    the validated setting for the ROOT run, and the node spans nested under it follow. Overriding
    only the children - which is what naming the project on three node decorators and not on the
    root amounts to - cannot move a trace anywhere.
    """
    graph = FakeGraph([{"event": "on_chat_model_stream", "data": {"chunk": Chunk("hi")}}])
    runs: list[Any] = []

    with tracing_context(enabled="local", client=offline_langsmith_client):
        async for _ in event_stream(
            graph,
            "q",
            "tenant-a",
            "t-1",
            {},
            langsmith_extra={"on_end": runs.append, "project_name": "taxcalc-agent-svc-prod"},
        ):
            pass

    assert runs[0].session_name == "taxcalc-agent-svc-prod"
