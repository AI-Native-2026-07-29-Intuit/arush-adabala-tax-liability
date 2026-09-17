# taxcalc-mcp-server/tests/test_schemas.py
"""The published contract: strict schemas, exact money, and the error-code table.

These are the tests that fail when the *contract* moves, as opposed to when the code breaks.
Every one of them asserts something a downstream LLM client depends on and cannot see for
itself: that an argument it invented will be rejected rather than dropped, that a money value it
sends survives the wire, and that the numeric code it branches on means the same thing whichever
tool raised it.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from mcp import McpError
from pydantic import ValidationError

from taxcalc_mcp_server.app import enforce_strict_tool_schemas, mcp
from taxcalc_mcp_server.errors import (
    DEFAULT_CODE,
    MAX_MESSAGE_CHARS,
    RAG_TIMEOUT_CODE,
    STATUS_TO_CODE,
    _map_http,
    rag_timeout,
)
from taxcalc_mcp_server.tools import _resources, llm, orders, rag  # noqa: F401 - registration

#: The four tools this server publishes. A literal list, not derived from the registry, so that
#: deleting or renaming a tool fails here instead of silently agreeing with itself.
EXPECTED_TOOLS = frozenset(
    {"orders.get_order", "orders.create_refund", "llm.chat", "rag.retrieve_and_generate"}
)


@pytest.fixture(scope="module", autouse=True)
def _hardened() -> None:
    """Apply the same schema hardening the transports apply, once, before any test reads a schema.

    Without this the tests would assert against schemas that the *running server* never
    publishes, which is the kind of green suite that proves nothing.
    """
    assert enforce_strict_tool_schemas(mcp) == len(EXPECTED_TOOLS)


def _schemas() -> dict[str, dict[str, object]]:
    """Return the published input schema for each tool, keyed by tool name.

    :returns: What a client sees in its ``tools/list`` response.
    """
    return {t.name: t.inputSchema for t in asyncio.run(mcp.list_tools())}


def test_all_four_tools_are_published() -> None:
    """``tools/list`` returns exactly the four tools, no more and no fewer."""
    assert set(_schemas()) == EXPECTED_TOOLS


def test_every_input_schema_forbids_unknown_properties() -> None:
    """Every published schema carries ``additionalProperties: false``.

    This is the half a client can *see*. Paired with the enforcement test below, it means the
    advertised rule and the applied rule are the same rule.
    """
    for name, schema in _schemas().items():
        assert schema.get("additionalProperties") is False, f"{name} accepts unknown arguments"


def test_unknown_argument_is_rejected_not_silently_dropped() -> None:
    """A hallucinated or typo'd argument fails the call instead of being ignored.

    The failure mode without this: a model sends ``tenat_id``, the key is dropped, and the call
    fails on a missing required argument - or worse, succeeds against a default and acts on the
    wrong tenant. Either way the model is told something other than "that parameter does not
    exist", which is the one message that lets it correct itself.
    """
    with pytest.raises(Exception, match=r"extra_forbidden|Extra inputs"):
        asyncio.run(
            mcp.call_tool(
                "orders.get_order",
                {"order_id": "ord-synth-9001", "tenant_id": "tenant-a", "tenat_id": "tenant-b"},
            )
        )


def test_refund_amount_accepts_a_string_and_parses_to_exact_decimal() -> None:
    """``"10.00"`` becomes ``Decimal("10.00")`` - the value AND the scale survive."""
    args = orders.CreateRefundArgs(
        order_id="ord-synth-9001",
        amount="10.00",  # type: ignore[arg-type]  # the string form is the contract
        reason="duplicate",
        tenant_id="tenant-a",
        idempotency_key=uuid4(),
    )
    assert args.amount == Decimal("10.00")
    assert str(args.amount) == "10.00", "the scale is part of the money value, not formatting"


def test_refund_amount_rejects_more_precision_than_money_has() -> None:
    """``10.001`` is refused rather than rounded.

    Rounding here would refund a different amount than the caller asked for and tell nobody.
    """
    with pytest.raises(ValidationError, match="decimal places"):
        orders.CreateRefundArgs(
            order_id="ord-synth-9001",
            amount="10.001",  # type: ignore[arg-type]
            reason="duplicate",
            tenant_id="tenant-a",
            idempotency_key=uuid4(),
        )


def test_refund_amount_rejects_a_binary_float() -> None:
    """A JSON number is refused, because a float has already lost the exact value."""
    with pytest.raises(ValidationError, match="not a JSON number"):
        orders.CreateRefundArgs(
            order_id="ord-synth-9001",
            amount=10.00,  # type: ignore[arg-type]  # exactly the mistake being guarded
            reason="duplicate",
            tenant_id="tenant-a",
            idempotency_key=uuid4(),
        )


def test_refund_requires_an_idempotency_key() -> None:
    """Omitting the key is a validation error, not a defaulted one.

    A generated default would make every retry a *new* key and therefore a second refund - the
    exact double-debit the key exists to prevent, arrived at by being helpful.
    """
    with pytest.raises(ValidationError, match="idempotency_key"):
        orders.CreateRefundArgs(
            order_id="ord-synth-9001",
            amount="10.00",  # type: ignore[arg-type]
            reason="duplicate",
            tenant_id="tenant-a",
        )  # type: ignore[call-arg]


def test_malformed_tenant_is_rejected_before_any_http_call() -> None:
    """``tenant-d`` fails schema validation; no request is ever built.

    Validation order is the point: a request never sent cannot leak one tenant's identifier into
    another tenant's upstream logs.
    """
    with pytest.raises(ValidationError, match="tenant_id"):
        orders.GetOrderArgs(order_id="ord-synth-9001", tenant_id="tenant-d")


@pytest.mark.parametrize(("status", "code"), sorted(STATUS_TO_CODE.items()))
def test_every_mapped_status_round_trips(status: int, code: int) -> None:
    """Each row of the mapping table produces its code, through the real function."""
    err = _map_http(status, "upstream said no")
    assert isinstance(err, McpError)
    assert err.error.code == code


@pytest.mark.parametrize("status", [500, 502, 503, 418])
def test_unmapped_statuses_fall_back_to_the_default_code(status: int) -> None:
    """Anything with no row - 5xx included - is the generic internal code."""
    assert _map_http(status, "boom").error.code == DEFAULT_CODE


def test_401_and_403_share_one_code() -> None:
    """Both mean "this JWT cannot do this", and neither is fixable by retrying."""
    assert _map_http(401, "").error.code == _map_http(403, "").error.code == 4030


def test_upstream_body_is_truncated_into_the_message() -> None:
    """A multi-kilobyte validation dump does not become a per-retry context-window cost."""
    err = _map_http(400, "x" * 5000)
    assert len(err.error.message) == MAX_MESSAGE_CHARS


def test_rag_timeout_has_its_own_code() -> None:
    """5040 is distinguishable from 5030, so "too slow" and "broken" are different decisions."""
    assert rag_timeout("rag timed out after 30.0s").error.code == RAG_TIMEOUT_CODE
    assert RAG_TIMEOUT_CODE != DEFAULT_CODE


def test_rag_answer_drops_chunk_text_from_citations() -> None:
    """The pre-shaped DTO carries identifiers and scores, never the chunk bodies.

    The pipeline returns ``chunk_text`` on every citation. Letting it through would restate, in
    full, the text the answer was just generated from - doubling the token cost of every grounded
    answer to say the same thing twice.
    """
    assert set(rag.Citation.model_fields) == {"chunk_id", "doc_id", "score"}
    assert "chunk_text" not in rag.Citation.model_fields


def test_doc_id_is_recovered_from_the_chunk_id() -> None:
    """``chunk-{doc_id}-p{n}`` yields ``doc_id``; an unknown shape yields the id unchanged."""
    assert rag._doc_id_of("chunk-form-1040-p3") == "form-1040"
    assert rag._doc_id_of("not-a-chunk-id") == "not-a-chunk-id"
