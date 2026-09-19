# taxcalc-agent-svc/tests/test_sse.py
"""The SSE bridge emits the three useChat channels, and the two errors stay distinguishable.

The channel prefixes are wire format: the W4 D4 React client parses ``0:``, ``2:`` and ``3:`` and
branches on them. A change here is a breaking API change for a client that lives in another
project and will not be recompiled against this one, which is why the frames are asserted on
byte-for-byte rather than through a helper that could drift alongside the code it checks.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langgraph.errors import GraphRecursionError

from taxcalc_agent_svc.budgets import BudgetExceeded
from taxcalc_agent_svc.sse import (
    DATA_CHANNEL,
    ERROR_BUDGET,
    ERROR_CHANNEL,
    ERROR_RECURSION,
    TEXT_CHANNEL,
    event_stream,
    frame,
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

    def __init__(self, events: list[dict[str, Any]], raises: Exception | None = None) -> None:
        """Construct a fake graph.

        :param events: The events to replay.
        :param raises: An exception to raise after replaying them.
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
