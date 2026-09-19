# taxcalc-agent-svc/tests/test_nodes.py
"""The node bodies: tenancy injection, idempotency determinism, and the typed answer's schema.

The api node's argument shaping gets the most attention here because it is the one place in this
service that is a **security boundary**. The model proposes tool arguments, and a model that has
read a document mentioning another tenant can propose that tenant's id. Everything else in this
file is about failures that cost money or correctness; this one is about a failure that leaks
data.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import StubCatalogue, StubTool
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from taxcalc_agent_svc.deps import budget_guard, mcp_session
from taxcalc_agent_svc.nodes.api import (
    IDEMPOTENCY_ARG,
    TENANT_ARG,
    declared_properties,
    idempotency_key,
    inject_context,
    tools_for_claude,
)
from taxcalc_agent_svc.nodes.retrieval import doc_id_of, shape_docs
from taxcalc_agent_svc.nodes.synthesis import (
    REFUSAL_CONFIDENCE,
    Citation,
    FinalAnswer,
    build_user_prompt,
)
from taxcalc_agent_svc.state import AgentState

# ------------------------------------------------------------------ idempotency determinism


def test_the_same_logical_call_always_produces_the_same_key() -> None:
    """Determinism is the whole point: a fresh key per attempt makes every retry a new refund."""
    a = idempotency_key("t-1", "orders.create_refund", {"order_id": "o1", "amount": "10.00"})
    b = idempotency_key("t-1", "orders.create_refund", {"order_id": "o1", "amount": "10.00"})
    assert a == b


def test_key_order_does_not_change_the_key() -> None:
    """``sort_keys=True`` and not ``str(dict)``.

    Two dicts with the same contents in a different insertion order must hash identically -
    ``str()`` on a dict is insertion-ordered, so using it would make the key depend on how the
    model happened to emit its arguments.
    """
    a = idempotency_key("t-1", "tool", {"a": 1, "b": 2})
    b = idempotency_key("t-1", "tool", {"b": 2, "a": 1})
    assert a == b


def test_a_different_thread_is_a_different_refund() -> None:
    """The key de-duplicates RETRIES, never DISTINCT requests.

    Two conversations issuing an identical refund are two refunds. A key that ignored the thread
    would silently collapse them into one and under-refund a customer.
    """
    a = idempotency_key("t-1", "tool", {"x": 1})
    b = idempotency_key("t-2", "tool", {"x": 1})
    assert a != b


def test_different_arguments_are_different_calls() -> None:
    """A changed amount is a changed call, not a retry of the old one."""
    a = idempotency_key("t-1", "orders.create_refund", {"amount": "10.00"})
    b = idempotency_key("t-1", "orders.create_refund", {"amount": "20.00"})
    assert a != b


def test_the_key_is_a_uuid5_string() -> None:
    """Version 5, so it is derived from the payload rather than randomly generated."""
    import uuid

    key = idempotency_key("t-1", "tool", {"x": 1})
    assert uuid.UUID(key).version == 5


# ------------------------------------------------------------------ schema-driven injection


def test_the_catalogue_is_reshaped_into_anthropic_tool_definitions(
    four_tools: list[StubTool],
) -> None:
    """MCP's ``inputSchema`` becomes Anthropic's ``input_schema`` - a rename, not a translation."""
    tools = tools_for_claude(StubCatalogue(four_tools))
    assert {t["name"] for t in tools} == {
        "orders.get_order",
        "orders.create_refund",
        "llm.chat",
        "rag.retrieve_and_generate",
    }
    assert all("input_schema" in t for t in tools)


def test_declared_properties_reads_the_published_schema(four_tools: list[StubTool]) -> None:
    """Only ``orders.create_refund`` declares an idempotency key - as the live D4 server does."""
    tools = tools_for_claude(StubCatalogue(four_tools))
    assert IDEMPOTENCY_ARG in declared_properties(tools, "orders.create_refund")
    assert IDEMPOTENCY_ARG not in declared_properties(tools, "orders.get_order")
    assert TENANT_ARG in declared_properties(tools, "rag.retrieve_and_generate")


def test_an_unknown_tool_declares_nothing_rather_than_raising(four_tools: list[StubTool]) -> None:
    """A hallucinated tool name injects nothing; the call fails upstream with the server's error.

    Raising here would replace a legible "no such tool" from the MCP server with a KeyError from
    this module, three frames from anything the model did.
    """
    assert declared_properties(tools_for_claude(StubCatalogue(four_tools)), "nope") == set()


def test_tenancy_is_overwritten_not_defaulted() -> None:
    """THE security assertion.

    A model that has read a document naming ``tenant-b`` can propose ``tenant_id="tenant-b"``.
    Injection overwrites it with the request's own tenant, unconditionally, after the model has
    spoken. `setdefault` semantics would let the model's value stand and reach another tenant's
    data.
    """
    sent = inject_context(
        {"order_id": "o1", TENANT_ARG: "tenant-b-ATTACKER"},
        {"order_id", TENANT_ARG},
        tenant_id="tenant-a",
        thread_id="t-1",
        tool="orders.get_order",
    )
    assert sent[TENANT_ARG] == "tenant-a"


def test_nothing_is_injected_into_a_tool_that_does_not_declare_it() -> None:
    """The D4 server rejects unknown arguments, so blind injection would fail every such call."""
    sent = inject_context(
        {"x": 1}, {"x"}, tenant_id="tenant-a", thread_id="t-1", tool="some.tool"
    )
    assert sent == {"x": 1}


def test_an_idempotency_key_is_added_only_where_declared() -> None:
    """The write tool gets one; the read tool does not."""
    write = inject_context(
        {"order_id": "o1"},
        {"order_id", TENANT_ARG, IDEMPOTENCY_ARG},
        tenant_id="tenant-a",
        thread_id="t-1",
        tool="orders.create_refund",
    )
    read = inject_context(
        {"order_id": "o1"},
        {"order_id", TENANT_ARG},
        tenant_id="tenant-a",
        thread_id="t-1",
        tool="orders.get_order",
    )
    assert IDEMPOTENCY_ARG in write
    assert IDEMPOTENCY_ARG not in read


def test_the_key_is_hashed_over_the_models_proposal_not_the_sent_payload() -> None:
    """A key derived from a payload containing that key cannot be recomputed by a verifier.

    So the hash is taken over the arguments BEFORE injection - which is exactly what an auditor
    re-deriving the key from the trace would have.
    """
    proposed = {"order_id": "o1", "amount": "10.00"}
    sent = inject_context(
        dict(proposed),
        {"order_id", "amount", TENANT_ARG, IDEMPOTENCY_ARG},
        tenant_id="tenant-a",
        thread_id="t-1",
        tool="orders.create_refund",
    )
    assert sent[IDEMPOTENCY_ARG] == idempotency_key("t-1", "orders.create_refund", proposed)


def test_injection_does_not_mutate_the_models_proposal() -> None:
    """The original is what the trace shows and what the hash is over; it must survive intact."""
    proposed = {"order_id": "o1"}
    inject_context(
        proposed, {"order_id", TENANT_ARG}, tenant_id="tenant-a", thread_id="t", tool="t"
    )
    assert proposed == {"order_id": "o1"}


# -------------------------------------------------------------------------- retrieval shaping


@pytest.mark.parametrize(
    ("chunk_id", "expected"),
    [
        ("chunk-doc-1-p0", "doc-1"),
        ("chunk-policy-2024-p17", "policy-2024"),
        ("not-a-chunk-id", "not-a-chunk-id"),
        ("", ""),
    ],
)
def test_doc_id_is_recovered_tolerantly(chunk_id: str, expected: str) -> None:
    """An unexpected id shape yields the id itself rather than raising.

    A citation with an imperfect ``doc_id`` is far better than a whole answer lost to a parse
    error over a display field.
    """
    assert doc_id_of(chunk_id) == expected


def test_only_three_fields_cross_into_the_state() -> None:
    """Chunk text and embeddings are dropped.

    Every state slot is written into a checkpoint row on EVERY super-step; carrying embeddings
    would make each checkpoint hundreds of kilobytes of numbers nothing reads.
    """
    raw = {
        "citations": [
            {
                "chunk_id": "chunk-doc-1-p0",
                "score": 0.9,
                "chunk_text": "x" * 5000,
                "embedding": [0.1] * 768,
            }
        ]
    }
    docs = shape_docs(raw, 8)
    assert docs == [{"chunk_id": "chunk-doc-1-p0", "doc_id": "doc-1", "score": 0.9}]


def test_shaping_truncates_to_top_k() -> None:
    """More candidates than asked for are dropped, not carried."""
    raw = {"citations": [{"chunk_id": f"chunk-d-p{i}", "score": 0.1} for i in range(20)]}
    assert len(shape_docs(raw, 8)) == 8


def test_a_pipeline_that_returns_nothing_shapes_to_an_empty_list() -> None:
    """The refusal path's input, not an exception."""
    assert shape_docs({}, 8) == []
    assert shape_docs({"citations": "unexpected"}, 8) == []


def test_a_missing_score_degrades_one_field_not_the_whole_citation() -> None:
    """Tolerance again: a renamed key costs a display value, not the answer."""
    docs = shape_docs({"citations": [{"chunk_id": "chunk-d-p0"}]}, 8)
    assert docs[0]["score"] == 0.0


# --------------------------------------------------------------------------- the typed answer


def test_the_answer_model_forbids_invented_fields() -> None:
    """Without ``extra="forbid"`` an invented ``sources`` key is dropped silently.

    The answer would then ship with an empty citation list and no error anywhere - and
    Instructor's retry, which repairs validation errors, would have nothing to repair.
    """
    with pytest.raises(ValidationError) as exc:
        FinalAnswer(text="x", citations=[], confidence=0.5, sources=["doc-1"])  # type: ignore[call-arg]
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


def test_citations_default_to_a_fresh_list_per_instance() -> None:
    """``default_factory=list``, not ``= []``.

    A shared mutable default is shared by every instance ever constructed, so one answer's
    citations would appear on the next.
    """
    a, b = FinalAnswer(text="a", confidence=0.5), FinalAnswer(text="b", confidence=0.5)
    a.citations.append(Citation(doc_id="d", quote="a supporting line"))
    assert b.citations == []


@pytest.mark.parametrize(
    ("quote", "why"), [("short", "string_too_short"), ("x" * 241, "string_too_long")]
)
def test_a_quote_must_be_a_real_span(quote: str, why: str) -> None:
    """Bounded at both ends: too short supports nothing, too long is a pasted chunk.

    Asserting on pydantic's error TYPE rather than its prose - the message wording changes
    between minor releases, and a test pinned to it fails on an upgrade that broke nothing.
    """
    with pytest.raises(ValidationError) as exc:
        Citation(doc_id="d", quote=quote)
    assert exc.value.errors()[0]["type"] == why


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_is_a_probability(confidence: float) -> None:
    """Out of 0-1 there is nothing for the refusal threshold to mean."""
    with pytest.raises(ValidationError):
        FinalAnswer(text="x", confidence=confidence)


def test_the_refusal_threshold_is_a_named_constant() -> None:
    """The prompt, the eval and any consumer branch on ONE value rather than three copies."""
    assert 0.0 < REFUSAL_CONFIDENCE < 1.0


def test_the_prompt_states_the_document_count_explicitly() -> None:
    """"0 documents" is the signal that triggers the refusal path.

    A model reading an empty list often narrates around it; a model told there are zero
    documents has been given the fact the prompt's refusal rule is written against.
    """
    prompt = build_user_prompt(AgentState(question="q", tenant_id="tenant-a", thread_id="t"))
    assert "Docs (0)" in prompt


def test_the_prompt_carries_the_tenant_and_the_evidence() -> None:
    """Everything the model is allowed to answer from, and nothing else."""
    state = AgentState(
        question="what is the refund policy",
        tenant_id="tenant-a",
        thread_id="t",
        docs=[{"chunk_id": "chunk-d-p0", "doc_id": "d", "score": 0.9}],
        tool_results={"orders.get_order": {"id": "ord-1"}},
    )
    prompt = build_user_prompt(state)
    assert "tenant-a" in prompt
    assert "Docs (1)" in prompt
    assert "ord-1" in prompt


def test_the_answer_round_trips_through_json() -> None:
    """The node stores the answer as a JSON string so it survives the checkpointer's serde."""
    answer = FinalAnswer(
        text="the answer",
        citations=[Citation(doc_id="d", quote="a supporting line")],
        confidence=0.9,
    )
    assert json.loads(answer.model_dump_json())["citations"][0]["doc_id"] == "d"


# ------------------------------------------------------------------------- the config seams


def test_a_missing_budget_guard_raises_rather_than_defaulting(guard: Any) -> None:
    """Quietly substituting a fresh guard would give every node a FULL budget.

    That is a silently unbounded run - the precise failure the guard exists to prevent.
    """
    with pytest.raises(KeyError, match="budget_guard"):
        budget_guard(RunnableConfig(configurable={}))


def test_a_missing_session_raises_where_it_can_be_understood() -> None:
    """A ``None`` would surface as an AttributeError three frames deeper, in SDK code."""
    with pytest.raises(KeyError, match="mcp_session"):
        mcp_session(RunnableConfig(configurable={}))


def test_the_seams_read_what_run_config_writes(settings: Any, guard: Any) -> None:
    """The producer and the consumers agree - asserted, not assumed."""
    from taxcalc_agent_svc.graph import run_config

    cfg = run_config("t-1", settings, guard=guard, session="a-session")
    assert budget_guard(cfg) is guard
    assert mcp_session(cfg) == "a-session"
    assert cfg["recursion_limit"] == settings.recursion_limit
    assert cfg["configurable"]["thread_id"] == "t-1"
