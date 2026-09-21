# taxcalc-agent-svc/tests/test_answer_streaming.py
"""The ``0:`` channel carries prose, from the synthesis node to the wire.

**This channel was dead.** The bridge filtered ``on_chat_model_stream``, which
``astream_events`` emits only for LangChain ``BaseChatModel`` runnables - and every model call in
this service goes through the Anthropic SDK directly. The real graph emitted ``on_chain_start``,
``on_chain_stream`` and ``on_chain_end``, nothing else, so the client received a single ``2:``
frame at the end and no token ever appeared as it was written. The unit tests passed throughout,
because their fake graphs replayed ``on_chat_model_stream`` events that no real graph could
produce - a fixture asserting a fixture.

Two properties are checked here, and the second is the one that was missing before:

1. The node turns a stream of arguments-JSON fragments into prose deltas (and the bridge turns
   those into ``0:`` frames).
2. **The real graph actually emits them.** ``test_the_real_graph_emits_text_deltas`` runs the
   production graph - real topology, real decorators, real bridge - and asserts on the frames
   that come out. It is the test that would have caught the original defect, and it fails
   against any design where nothing in the graph produces a text event.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import StubSession
from langchain_core.runnables import RunnableConfig
from pydantic import SecretStr
from test_cost_attribution import ANSWER_PAYLOAD, RecordingAnthropic

from taxcalc_agent_svc.budgets import BudgetGuard
from taxcalc_agent_svc.deps import BUDGET_GUARD_KEY, MCP_SESSION_KEY
from taxcalc_agent_svc.graph import build_graph
from taxcalc_agent_svc.nodes import api as api_mod
from taxcalc_agent_svc.nodes import retrieval as retrieval_mod
from taxcalc_agent_svc.nodes import synthesis as synthesis_mod
from taxcalc_agent_svc.nodes.synthesis import (
    ANSWER_DELTA_EVENT,
    TOOL_NAME,
    _growing_text,
)
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.sse import TEXT_CHANNEL, event_stream
from taxcalc_agent_svc.state import AgentState

ANSWER_TEXT: str = ANSWER_PAYLOAD["text"]


# ------------------------------------------------------------------ partial JSON extraction


def test_the_text_field_is_recovered_from_a_half_arrived_json() -> None:
    """Mid-stream the buffer is not valid JSON, and that is the normal case, not the edge case."""
    assert _growing_text('{"text": "Exclusive and regu') == "Exclusive and regu"


def test_nothing_is_recovered_before_the_field_arrives() -> None:
    """An empty prefix yields no delta rather than a spurious empty one."""
    assert _growing_text("") == ""
    assert _growing_text('{"confi') == ""


def test_a_completed_json_yields_the_whole_text() -> None:
    """The last fragment closes the object; the text is then simply the field."""
    assert _growing_text(json.dumps(ANSWER_PAYLOAD)) == ANSWER_TEXT


def test_a_non_string_text_field_is_ignored_rather_than_rendered() -> None:
    """A model that emits the wrong type for ``text`` must not put ``None`` on the wire."""
    assert _growing_text('{"text": 42, ') == ""


def test_the_extraction_is_monotonic_across_a_whole_stream() -> None:
    """The prose only ever grows, which is what makes "send the new suffix" correct.

    If the parse ever shortened - a partial-mode quirk, an escape sequence mid-flight - the
    node's ``len(text) > sent`` guard would stall and the tail of the answer would never be
    sent. Asserted over every prefix of a real arguments JSON rather than over a handful of
    hand-picked ones.
    """
    raw = json.dumps(ANSWER_PAYLOAD)
    lengths = [len(_growing_text(raw[:i])) for i in range(1, len(raw) + 1)]
    assert lengths == sorted(lengths)
    assert lengths[-1] == len(ANSWER_TEXT)


# ------------------------------------------------------------------ the node emits deltas


@pytest.fixture
def settings() -> Settings:
    """Settings with a syntactically valid key and no network behind it.

    :returns: Validated settings.
    """
    return Settings(anthropic_api_key=SecretStr("sk-ant-test"))


async def test_the_node_dispatches_prose_deltas_that_reassemble_into_the_answer(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Concatenating every delta reproduces the answer text exactly - no gaps, no repeats.

    Asserted on the JOIN rather than on the individual fragments: how the text is chopped up is
    the model's business and changes with every completion, while "the client that appends these
    ends up with the answer" is the actual contract.
    """
    deltas: list[str] = []

    async def capture(name: str, payload: dict[str, Any], **_kw: Any) -> None:
        """Record a dispatched event instead of routing it to a callback manager."""
        assert name == ANSWER_DELTA_EVENT
        deltas.append(payload["delta"])

    monkeypatch.setattr(synthesis_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(synthesis_mod, "adispatch_custom_event", capture)

    state: AgentState = {"question": "what is the rule", "tenant_id": "t-1", "docs": []}
    config = RunnableConfig(configurable={BUDGET_GUARD_KEY: BudgetGuard()})
    result = await synthesis_mod._synthesis(state, config, settings)

    assert "".join(deltas) == ANSWER_TEXT
    assert len(deltas) > 1, "the answer arrived in one lump; nothing was streamed"
    assert json.loads(result["answer"])["text"] == ANSWER_TEXT


async def test_the_answer_survives_a_stream_that_cannot_be_dispatched(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """No parent run - the smoke script, an eval, a direct call - still produces the answer.

    The deltas are cosmetic; the answer is not. Trading the response for its progress bar would
    be the wrong way round, so the dispatch failure is swallowed and the node returns normally.
    """
    monkeypatch.setattr(synthesis_mod, "AsyncAnthropic", RecordingAnthropic)

    state: AgentState = {"question": "what is the rule", "tenant_id": "t-1", "docs": []}
    config = RunnableConfig(configurable={BUDGET_GUARD_KEY: BudgetGuard()})
    # No run context at all: adispatch_custom_event raises RuntimeError internally.
    result = await synthesis_mod._synthesis(state, config, settings)

    assert json.loads(result["answer"])["text"] == ANSWER_TEXT


async def test_the_model_is_forced_to_call_the_answer_tool(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """``tool_choice`` is forced, so a model cannot reply in prose and bypass the schema."""
    monkeypatch.setattr(synthesis_mod, "AsyncAnthropic", RecordingAnthropic)

    state: AgentState = {"question": "what is the rule", "tenant_id": "t-1", "docs": []}
    config = RunnableConfig(configurable={BUDGET_GUARD_KEY: BudgetGuard()})
    await synthesis_mod._synthesis(state, config, settings)

    assert RecordingAnthropic.stream_kwargs[0]["tool_choice"] == {
        "type": "tool",
        "name": TOOL_NAME,
    }


# ------------------------------------------------------------------ end to end, real graph


async def test_the_real_graph_emits_text_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production graph, through the production bridge, produces ``0:`` frames.

    **The regression test for the original defect.** Every other test in this file could pass
    while the wired-up system emitted nothing on channel 0, because each of them stops short of
    the graph. This one builds the real graph, streams it through the real
    :func:`~taxcalc_agent_svc.sse.event_stream`, and asserts on the bytes - so a synthesis node
    that stopped dispatching, or a bridge that stopped listening, fails here.

    Only the Anthropic client and the retrieval pipeline are substituted; the topology, the
    decorators, the reducers and the bridge are the shipped ones.
    """
    monkeypatch.setattr(synthesis_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(retrieval_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(api_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(
        "taxcalc_agent_svc.retrievers.run_pipeline", lambda _q, _t, _k: {"citations": []}
    )
    monkeypatch.setenv("RERANKER", "bge")

    settings = Settings(anthropic_api_key=SecretStr("sk-ant-test"))
    graph = build_graph(settings)
    config: dict[str, Any] = {
        "configurable": {
            BUDGET_GUARD_KEY: BudgetGuard(),
            MCP_SESSION_KEY: StubSession([], {}),
            "thread_id": "t-stream-1",
        },
        "recursion_limit": settings.recursion_limit,
    }

    frames = [
        f
        async for f in event_stream(
            graph, "what is the home office deduction rule", "tenant-a", "t-stream-1", config
        )
    ]

    text_frames = [f for f in frames if f.startswith(f"{TEXT_CHANNEL}:".encode())]
    data_frames = [f for f in frames if f.startswith(b"2:")]

    assert text_frames, "the real graph emitted no 0: deltas - the text channel is dead again"
    assert data_frames, "the real graph emitted no 2: finalAnswer"
    # The deltas reassemble into the same answer the 2: frame carries.
    streamed = "".join(json.loads(f[2:]) for f in text_frames)
    final = json.loads(data_frames[0][2:])["finalAnswer"]
    assert streamed == final["text"] == ANSWER_TEXT


async def test_the_deltas_arrive_before_the_final_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordering is the entire point of streaming: prose first, typed answer last.

    A bridge that buffered and emitted both at the end would satisfy every content assertion
    above while giving the user exactly the blank wait the ``0:`` channel exists to remove.
    """
    monkeypatch.setattr(synthesis_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(retrieval_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(api_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(
        "taxcalc_agent_svc.retrievers.run_pipeline", lambda _q, _t, _k: {"citations": []}
    )
    monkeypatch.setenv("RERANKER", "bge")

    settings = Settings(anthropic_api_key=SecretStr("sk-ant-test"))
    graph = build_graph(settings)
    config: dict[str, Any] = {
        "configurable": {
            BUDGET_GUARD_KEY: BudgetGuard(),
            MCP_SESSION_KEY: StubSession([], {}),
            "thread_id": "t-stream-2",
        },
        "recursion_limit": settings.recursion_limit,
    }

    channels = [
        f[:1]
        async for f in event_stream(
            graph, "what is the home office deduction rule", "tenant-a", "t-stream-2", config
        )
    ]

    assert b"2" in channels
    assert channels.index(b"0") < channels.index(b"2")
