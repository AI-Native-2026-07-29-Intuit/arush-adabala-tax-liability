# taxcalc-agent-svc/tests/test_cost_attribution.py
"""Every Claude call is tagged for the proxy and billed to the request's guard.

Two mechanisms, one subject. The ``X-Agent`` header is how cost is attributed **outside** this
process - the W3 D1 llm-proxy reads it and emits ``retrieval_cost_per_request``,
``api_cost_per_request`` and ``synthesis_cost_per_request`` as separate CloudWatch SLIs, each
with its own alarm threshold. :class:`~taxcalc_agent_svc.budgets.BudgetGuard` is how cost is
attributed **inside** it, in time to refuse the next call.

Both had the same gap: present at one call site and assumed at the others. The header was written
in all three nodes and asserted in none, so a node that lost it during a refactor would collapse
three CloudWatch series into one with nothing going red. ``record_call`` was worse than unasserted
- it was genuinely absent from two of the three nodes, so retrieval and synthesis were checked
against the ceiling and never added to it. The file that follows exists so neither can regress
quietly.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import pytest
from anthropic.types import (
    InputJSONDelta,
    Message,
    RawContentBlockDeltaEvent,
    ToolUseBlock,
    Usage,
)
from conftest import StubSession
from langchain_core.runnables import RunnableConfig
from pydantic import SecretStr

from taxcalc_agent_svc.budgets import BudgetExceeded, BudgetGuard
from taxcalc_agent_svc.deps import BUDGET_GUARD_KEY, MCP_SESSION_KEY
from taxcalc_agent_svc.nodes import api as api_mod
from taxcalc_agent_svc.nodes import retrieval as retrieval_mod
from taxcalc_agent_svc.nodes import synthesis as synthesis_mod
from taxcalc_agent_svc.nodes.retrieval import RERANKER_ENV
from taxcalc_agent_svc.nodes.synthesis import TOOL_NAME
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.state import AgentState

#: A response whose usage is priced at exactly 1,500 e-5 USD by the guard's published rates:
#: 0 input tokens, 1,000 output at 1500 per 1,000 tokens. A round number so a failure reads as
#: "recorded nothing" or "recorded once" rather than as arithmetic.
OUT_TOKENS: int = 1_000
EXPECTED_E5: int = 1_500


class FakeUsage:
    """The two fields :meth:`BudgetGuard.record_call` reads off a response."""

    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        """Construct a usage block.

        :param input_tokens: Prompt tokens.
        :param output_tokens: Completion tokens.
        """
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeBlock:
    """One text block of an Anthropic response."""

    def __init__(self, text: str) -> None:
        """Construct a text block.

        :param text: The block's text.
        """
        self.type = "text"
        self.text = text


class FakeMessage:
    """A minimal stand-in for ``anthropic.types.Message``."""

    def __init__(self, text: str = "rewritten query", stop_reason: str = "end_turn") -> None:
        """Construct a message carrying priced usage.

        :param text: The reply text.
        :param stop_reason: Why the model stopped.
        """
        self.content = [FakeBlock(text)]
        self.usage = FakeUsage(0, OUT_TOKENS)
        self.stop_reason = stop_reason


class RecordingAnthropic:
    """An ``AsyncAnthropic`` stand-in that records how it was constructed.

    Constructed exactly as the real client is, so the assertion is on the *production* call - not
    on a constant a test and the code could both be reading from the same place while the wire
    carried something else.

    :cvar headers: Every ``default_headers`` mapping this class has been constructed with.
    """

    headers: ClassVar[list[dict[str, str]]] = []

    #: Every ``messages.stream`` call's arguments, minus the prompt.
    stream_kwargs: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        """Record the construction.

        :param kwargs: Whatever the node passed, including ``default_headers``.
        """
        RecordingAnthropic.headers.append(dict(kwargs.get("default_headers") or {}))
        self.messages = self

    async def create(self, **_kwargs: Any) -> FakeMessage:
        """Answer every completion with the same priced message.

        :returns: The canned message.
        """
        return FakeMessage()

    def stream(self, **kwargs: Any) -> FakeMessageStream:
        """Answer every streamed completion with the same scripted stream.

        Not ``async``: ``client.messages.stream(...)`` returns an async context manager
        synchronously, and a coroutine here would make ``async with`` fail in a way that looks
        like a bug in the node rather than in the stub.

        :param kwargs: Whatever the node passed; ``tool_choice`` is recorded for assertion.
        :returns: The scripted stream.
        """
        RecordingAnthropic.stream_kwargs.append(
            {k: v for k, v in kwargs.items() if k != "messages"}
        )
        return FakeMessageStream(ANSWER_PAYLOAD)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the recorder, and contain the retrieval node's write to the process environment.

    ``_retrieval`` sets ``RERANKER`` for the sidecar - a real assignment to ``os.environ`` that
    outlives the test that triggered it. Left alone it leaks: ``Settings`` reads that unprefixed
    name, so a later test asserting on a reranker it selected through
    ``TAXCALC_AGENT_RERANKER`` gets whatever this file happened to write instead, and fails in a
    file that never mentions this one. ``monkeypatch.setenv`` puts the variable under the
    fixture's control so it is restored - or removed - at teardown.

    :param monkeypatch: The patcher whose teardown restores the environment.
    """
    RecordingAnthropic.headers = []
    RecordingAnthropic.stream_kwargs = []
    monkeypatch.setenv(RERANKER_ENV, "bge")


@pytest.fixture
def settings() -> Settings:
    """Settings with a syntactically valid key and no network behind it.

    :returns: Validated settings.
    """
    return Settings(anthropic_api_key=SecretStr("sk-ant-test"))


def _config(guard: BudgetGuard, session: Any = None) -> RunnableConfig:
    """Build the config seam the node bodies read.

    :param guard: The request's cost ceiling.
    :param session: The MCP session, for the api node.
    :returns: The config.
    """
    return RunnableConfig(
        configurable={BUDGET_GUARD_KEY: guard, MCP_SESSION_KEY: session}
    )


# ------------------------------------------------------------------ X-Agent tagging


async def test_the_retrieval_client_is_tagged_for_the_proxy(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """``X-Agent: retrieval``, so ``retrieval_cost_per_request`` is its own CloudWatch series."""
    monkeypatch.setattr(retrieval_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(
        "taxcalc_agent_svc.retrievers.run_pipeline",
        lambda _q, _t, _k: {"citations": []},
    )
    state: AgentState = {"question": "what is the home office deduction rule", "tenant_id": "t-1"}
    await retrieval_mod._retrieval(state, _config(BudgetGuard()), settings)

    assert RecordingAnthropic.headers == [{"X-Agent": "retrieval"}]


async def test_the_synthesis_client_is_tagged_for_the_proxy(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """``X-Agent: synthesis``. Asserted on the client Instructor was handed, not on Instructor."""
    monkeypatch.setattr(synthesis_mod, "AsyncAnthropic", RecordingAnthropic)

    state: AgentState = {"question": "what is the rule", "tenant_id": "t-1", "docs": []}
    await synthesis_mod._synthesis(state, _config(BudgetGuard()), settings)

    assert RecordingAnthropic.headers == [{"X-Agent": "synthesis"}]


async def test_the_api_client_is_tagged_for_the_proxy(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """``X-Agent: api``, distinct from the other two."""
    monkeypatch.setattr(api_mod, "AsyncAnthropic", RecordingAnthropic)

    state: AgentState = {"question": "where is order ord-1", "tenant_id": "t-1"}
    await api_mod._api(state, _config(BudgetGuard(), StubSession([], {})), settings)

    assert RecordingAnthropic.headers == [{"X-Agent": "api"}]


def test_the_three_tags_are_three_distinct_values() -> None:
    """Each SLI has its own alarm threshold, which requires its own label.

    Read out of the three modules rather than restated here: a node that changed its tag would
    fail this, whereas a test carrying its own copy of the strings would pass forever.
    """
    import inspect

    tags = {
        mod.__name__: [
            line.split('"X-Agent": "')[1].split('"')[0]
            for line in inspect.getsource(mod).splitlines()
            if '"X-Agent": "' in line
        ]
        for mod in (retrieval_mod, api_mod, synthesis_mod)
    }
    found = [t for tag_list in tags.values() for t in tag_list]
    assert sorted(found) == ["api", "retrieval", "synthesis"], tags


# ------------------------------------------------------------------ in-process billing


async def test_retrieval_bills_its_rewrite_call_to_the_guard(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The query rewrite is a paid Claude call, and the tally says so.

    It previously did not. ``check_or_raise`` was called and ``record_call`` was not, so the node
    reported ``cost_usd_e5: 0`` no matter what the rewrite cost.
    """
    monkeypatch.setattr(retrieval_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(
        "taxcalc_agent_svc.retrievers.run_pipeline",
        lambda _q, _t, _k: {"citations": []},
    )
    guard = BudgetGuard()
    state: AgentState = {"question": "what is the home office deduction rule", "tenant_id": "t-1"}
    result = await retrieval_mod._retrieval(state, _config(guard), settings)

    assert guard.spent_usd_e5 == EXPECTED_E5
    assert result["cost_usd_e5"] == EXPECTED_E5


async def test_synthesis_bills_its_completion_to_the_guard(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The most expensive node in the graph contributes to the ceiling it is checked against."""
    monkeypatch.setattr(synthesis_mod, "AsyncAnthropic", RecordingAnthropic)

    guard = BudgetGuard()
    state: AgentState = {"question": "what is the rule", "tenant_id": "t-1", "docs": []}
    result = await synthesis_mod._synthesis(state, _config(guard), settings)

    assert guard.spent_usd_e5 == EXPECTED_E5
    assert result["cost_usd_e5"] == EXPECTED_E5


async def test_a_failed_rewrite_bills_nothing(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """A call that never reached Anthropic costs nothing.

    The rewrite is best-effort and swallows its own failure; the accounting must swallow it too,
    or a flapping dependency would spend a caller's budget on calls that never happened.
    """

    class FailingAnthropic(RecordingAnthropic):
        """A client whose completions always fail."""

        async def create(self, **_kwargs: Any) -> FakeMessage:
            """Fail the way a transport error does.

            :raises RuntimeError: always.
            """
            raise RuntimeError("upstream is down")

    monkeypatch.setattr(retrieval_mod, "AsyncAnthropic", FailingAnthropic)
    monkeypatch.setattr(
        "taxcalc_agent_svc.retrievers.run_pipeline",
        lambda _q, _t, _k: {"citations": []},
    )
    guard = BudgetGuard()
    state: AgentState = {"question": "what is the home office deduction rule", "tenant_id": "t-1"}
    result = await retrieval_mod._retrieval(state, _config(guard), settings)

    assert guard.spent_usd_e5 == 0
    assert result["cost_usd_e5"] == 0


async def test_the_docs_only_path_can_exhaust_the_ceiling(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The deliverable's claim, on the path that could not previously make it true.

    A docs-only question never touches the api node - which was, until both fixes above, the only
    node feeding the tally. So on the most common question this service answers, cumulative spend
    stayed at zero for the life of the request and the dollar ceiling was unreachable by
    construction: "the BudgetGuard cuts the run earlier if the dollar ceiling fires first" held
    only for questions that happened to call a tool.

    Here retrieval alone - both of its Claude calls, the rewrite and the pipeline's generation -
    drives the guard exactly onto a deliberately small ceiling, and the breach surfaces as
    ``BudgetExceeded`` out of synthesis, which the SSE bridge renders as its own ``3:`` error
    code rather than as a truncated answer.
    """
    monkeypatch.setattr(retrieval_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(synthesis_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(
        "taxcalc_agent_svc.retrievers.run_pipeline",
        lambda _q, _t, _k: {
            "citations": [],
            "usage": {"input_tokens": 2_000, "output_tokens": 500},
        },
    )

    # Exactly what retrieval's two calls cost: the rewrite (1500) plus the generation (1350).
    # Retrieval completes and lands on the ceiling; synthesis is refused before it spends.
    guard = BudgetGuard(EXPECTED_E5 + 1_350)
    state: AgentState = {"question": "what is the home office deduction rule", "tenant_id": "t-1"}
    cfg = _config(guard)

    await retrieval_mod._retrieval(state, cfg, settings)
    assert guard.spent_usd_e5 == guard.ceiling_usd_e5

    with pytest.raises(BudgetExceeded):
        await synthesis_mod._synthesis(state, cfg, settings)


ANSWER_PAYLOAD: dict[str, Any] = {
    "text": "Exclusive and regular use is required.",
    "citations": [{"doc_id": "doc-1", "quote": "exclusive and regular use"}],
    "confidence": 0.9,
}


class FakeMessageStream:
    """An ``AsyncMessages.stream`` stand-in built from real ``anthropic.types`` events.

    Real event types rather than duck-typed stand-ins, because the node narrows on
    ``event.type`` and ``isinstance(block, ToolUseBlock)`` - a hand-rolled object with the right
    attribute names would satisfy the code while proving nothing about the shapes the SDK
    actually delivers.

    The arguments JSON is sliced into fragments the way the wire delivers it, mid-token and
    mid-string, so the node's partial parse is exercised on the shape that breaks naive
    ``json.loads``.
    """

    def __init__(self, arguments: dict[str, Any], chunk_size: int = 9) -> None:
        """Construct a stream that will emit ``arguments`` in fragments.

        :param arguments: The tool arguments the model "produces".
        :param chunk_size: Characters per fragment.
        """
        raw = json.dumps(arguments)
        self._fragments = [raw[i : i + chunk_size] for i in range(0, len(raw), chunk_size)]
        self._arguments = arguments

    async def __aenter__(self) -> FakeMessageStream:
        """Enter the stream context.

        :returns: Itself.
        """
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Leave the stream context."""

    def __aiter__(self) -> AsyncIterator[RawContentBlockDeltaEvent]:
        """Emit one ``input_json_delta`` per fragment.

        :returns: The event iterator.
        """

        async def gen() -> AsyncIterator[RawContentBlockDeltaEvent]:
            for fragment in self._fragments:
                yield RawContentBlockDeltaEvent(
                    type="content_block_delta",
                    index=0,
                    delta=InputJSONDelta(type="input_json_delta", partial_json=fragment),
                )

        return gen()

    async def get_final_message(self) -> Message:
        """Return the completed message, parsed as the SDK would parse it.

        :returns: The message, carrying the tool call and priced usage.
        """
        return Message(
            id="msg_1",
            content=[
                ToolUseBlock(
                    id="tu_1", name=TOOL_NAME, input=self._arguments, type="tool_use"
                )
            ],
            model="claude-sonnet-4-5",
            role="assistant",
            stop_reason="tool_use",
            stop_sequence=None,
            type="message",
            usage=Usage(input_tokens=0, output_tokens=OUT_TOKENS),
        )


class _FakeHandle:
    """Stands in for the pipeline's Postgres and Redis handles.

    Never used for anything - the test below opens no sockets and runs no queries. It exists so
    ``_clients`` has something to cache and narrow.
    """


async def test_the_pipelines_generation_client_is_tagged_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retrieval agent's SECOND Claude call carries the tag as well.

    ``retrieve_and_generate`` writes the answer text with its own long-lived client, built in
    :mod:`taxcalc_agent_svc.retrievers` rather than in the node - which is how it escaped an
    audit that checked the three node clients and found all three correct. Two completions per
    retrieval request with one of them unlabelled makes ``retrieval_cost_per_request`` an
    undercount rather than a missing metric: the series exists, looks healthy, and is wrong.

    The cache is emptied and the two non-Anthropic constructors are replaced, so this exercises
    the real ``_clients`` body - including the branch that only runs on first use - without
    opening a socket. ``psycopg.Connection`` and ``redis.Redis`` are rebound alongside them
    because the body narrows on those names after building the cache.
    """
    import taxcalc_agent_svc.retrievers as retrievers_mod

    monkeypatch.setattr(retrievers_mod, "_CLIENTS", {})
    monkeypatch.setattr("anthropic.Anthropic", RecordingAnthropic)
    # String targets: `psycopg` and `redis` are imports the module uses, not names it exports,
    # and reaching through the module object to them asserts a coupling it does not publish.
    monkeypatch.setattr("psycopg.connect", lambda _dsn: _FakeHandle())
    monkeypatch.setattr("psycopg.Connection", _FakeHandle)
    monkeypatch.setattr("redis.from_url", lambda _url: _FakeHandle())
    monkeypatch.setattr("redis.Redis", _FakeHandle)
    monkeypatch.setenv(retrievers_mod.PG_DSN_ENV, "postgresql://user@host/db")
    monkeypatch.setenv(retrievers_mod.REDIS_URL_ENV, "redis://host")

    client, _conn, _r = retrievers_mod._clients()

    assert isinstance(client, RecordingAnthropic)
    assert RecordingAnthropic.headers == [{"X-Agent": "retrieval"}]


def test_all_four_claude_clients_in_this_service_are_tagged() -> None:
    """Four call sites, not three - the count is the assertion.

    The three node clients were audited and correct while a fourth went untagged for the whole
    of the deliverable's life. Pinning the number means a fifth client added later fails here
    rather than quietly joining the unlabelled bucket.
    """
    import inspect

    from taxcalc_agent_svc import retrievers as retrievers_mod

    modules = (retrieval_mod, api_mod, synthesis_mod, retrievers_mod)
    tags = [
        line.split('"X-Agent": "')[1].split('"')[0]
        for mod in modules
        for line in inspect.getsource(mod).splitlines()
        if '"X-Agent": "' in line and not line.lstrip().startswith("#")
    ]
    assert sorted(tags) == ["api", "retrieval", "retrieval", "synthesis"]


# ------------------------------------------------------------------ the pipeline's generation


async def test_the_pipeline_generation_is_billed_to_the_guard(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The retrieval agent's second Claude call reaches the tally, not just the proxy.

    Tagging it made the CloudWatch series correct; this makes the per-request ceiling correct.
    They are different failures: an untagged call is attributed to the wrong SLI, an unbilled
    one is attributed to nothing at all and cannot trip the budget however much it costs.

    Two calls are billed here - the rewrite (1,000 output tokens) and the generation (a further
    2,000 input, 500 output) - so the assertion also pins that they ACCUMULATE rather than one
    overwriting the other.
    """
    monkeypatch.setattr(retrieval_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(
        "taxcalc_agent_svc.retrievers.run_pipeline",
        lambda _q, _t, _k: {
            "citations": [],
            "usage": {"input_tokens": 2_000, "output_tokens": 500},
        },
    )
    guard = BudgetGuard()
    state: AgentState = {"question": "what is the home office deduction rule", "tenant_id": "t-1"}
    result = await retrieval_mod._retrieval(state, _config(guard), settings)

    # rewrite: (0*300 + 1000*1500)//1000 = 1500. generation: (2000*300 + 500*1500)//1000 = 1350.
    assert guard.spent_usd_e5 == EXPECTED_E5 + 1_350
    assert result["cost_usd_e5"] == EXPECTED_E5 + 1_350


async def test_a_cache_hit_bills_nothing_for_a_generation_that_did_not_happen(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """No ``usage`` key means no completion, which means no charge.

    The pipeline's semantic cache returns a stored answer without calling Claude, and omits
    ``usage`` to say so. Were ``usage`` instead cached alongside the answer, every hit would
    replay the tokens of the call that first produced it - so the better the cache performed,
    the more phantom spend the guard would meter, and a hot query could exhaust a ceiling
    without ever reaching Anthropic.

    Only the rewrite is billed here.
    """
    monkeypatch.setattr(retrieval_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(
        "taxcalc_agent_svc.retrievers.run_pipeline",
        lambda _q, _t, _k: {"citations": [], "text": "a cached answer"},
    )
    guard = BudgetGuard()
    state: AgentState = {"question": "what is the home office deduction rule", "tenant_id": "t-1"}
    result = await retrieval_mod._retrieval(state, _config(guard), settings)

    assert guard.spent_usd_e5 == EXPECTED_E5
    assert result["cost_usd_e5"] == EXPECTED_E5


async def test_a_malformed_usage_block_is_ignored_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """A pipeline that reports usage in an unexpected shape must not fail the request.

    Same trade as ``record_call`` makes for a response without usage: under-counting a call the
    guard cannot read beats killing the request the guard exists to protect.
    """
    monkeypatch.setattr(retrieval_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr(
        "taxcalc_agent_svc.retrievers.run_pipeline",
        lambda _q, _t, _k: {"citations": [], "usage": "2000 in, 500 out"},
    )
    guard = BudgetGuard()
    state: AgentState = {"question": "what is the home office deduction rule", "tenant_id": "t-1"}
    await retrieval_mod._retrieval(state, _config(guard), settings)

    assert guard.spent_usd_e5 == EXPECTED_E5


async def test_an_exhausted_budget_stops_the_pipeline_before_it_generates(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The ceiling is re-checked between the rewrite and the generation.

    Checked once before the cheap call, a ceiling never guards the expensive one: a rewrite that
    consumed the whole budget would be followed by a generation costing whatever it liked. The
    pipeline must not even be entered.
    """
    entered = False

    def _pipeline(_q: str, _t: str, _k: int) -> dict[str, Any]:
        """Record that the expensive stage was reached.

        :returns: An empty result.
        """
        nonlocal entered
        entered = True
        return {"citations": []}

    monkeypatch.setattr(retrieval_mod, "AsyncAnthropic", RecordingAnthropic)
    monkeypatch.setattr("taxcalc_agent_svc.retrievers.run_pipeline", _pipeline)

    # Exactly one rewrite's worth of budget: the rewrite lands on the ceiling, the pipeline is
    # then refused.
    guard = BudgetGuard(EXPECTED_E5)
    state: AgentState = {"question": "what is the home office deduction rule", "tenant_id": "t-1"}
    with pytest.raises(BudgetExceeded):
        await retrieval_mod._retrieval(state, _config(guard), settings)

    assert not entered, "the pipeline generated on an exhausted budget"
