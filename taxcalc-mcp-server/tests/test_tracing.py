# taxcalc-mcp-server/tests/test_tracing.py
"""Every tool emits exactly one LangSmith run, named after itself, into the right project.

**What this can and cannot prove.** It captures the runs the LangSmith SDK would send, by
substituting the client's transport, and asserts their name, type and project. It does not prove
that LangSmith's servers accept and display them - that needs a real API key and is their side of
the contract, not this server's.

That split is the point. "The spans are visible in the LangSmith project" is a claim nobody can
check on a machine without credentials, so it tends to be asserted rather than tested and then
quietly stops being true - a decorator dropped in a refactor, a project name typo'd, a handler
split into two functions with the tracing left on the outer one. Every one of those is visible
right here, offline, on every CI run.

**Why it asserts the project by name rather than reading a constant.** ``taxcalc-mcp-server`` is
the project the W7 D3 RAG spans also land in, deliberately: a ``rag.retrieve_and_generate`` tool
call and the retrieval it triggers are one causal chain, and splitting them across two projects
means reading two timelines to answer one question. A test that read the name from the same
constant the code does would agree with a typo.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import httpx
import pytest
from langsmith import Client
from langsmith.run_helpers import tracing_context
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext

from taxcalc_mcp_server.app import AppCtx, enforce_strict_tool_schemas, mcp
from taxcalc_mcp_server.settings import Settings
from taxcalc_mcp_server.tools import _resources, llm, orders, rag  # noqa: F401 - registration

#: The LangSmith project every tool span must land in. Spelled out rather than imported from the
#: settings the code reads, so this test disagrees with a typo instead of sharing it.
EXPECTED_PROJECT = "taxcalc-mcp-server"


@pytest.fixture(scope="module", autouse=True)
def _hardened() -> None:
    """Apply the transports' startup hardening once."""
    assert enforce_strict_tool_schemas(mcp) == 4


@contextmanager
def capture_runs(
    upstream: httpx.Response, rag_result: dict[str, Any] | None = None
) -> Iterator[list[dict[str, Any]]]:
    """Run a tool with tracing on and a stubbed transport, collecting the runs it would send.

    :param upstream: The canned HTTP response the tool's outbound call receives.
    :param rag_result: Canned pipeline result for the retrieval tool.
    :yields: The captured ``create_run`` payloads.
    """
    captured: list[dict[str, Any]] = []
    settings = Settings()
    http = httpx.AsyncClient(
        base_url=settings.normalised_orders_url(),
        transport=httpx.MockTransport(lambda _: upstream),
    )
    token = request_ctx.set(
        RequestContext(
            request_id="tracing-test",
            meta=None,
            session=None,  # type: ignore[arg-type]  # no path under test touches the session
            lifespan_context=AppCtx(
                http=http,
                rag_fn=lambda *_a, **_k: rag_result or {},
                settings=settings,
            ),
        )
    )
    # Patched on the class: Client instances expose create_run as a read-only attribute, so the
    # substitution has to happen before the instance is built.
    with (
        mock.patch.object(
            Client,
            "create_run",
            autospec=True,
            side_effect=lambda _self, **kw: captured.append(kw),
        ),
        mock.patch.object(Client, "update_run", autospec=True, side_effect=lambda *_a, **_k: None),
    ):
        client = Client(api_key="fake", api_url="http://localhost:1", auto_batch_tracing=False)
        # tracing_context(enabled=True) overrides LANGSMITH_TRACING=false from conftest, which is
        # off everywhere else on purpose - no other test should be emitting runs at all.
        with tracing_context(enabled=True, client=client):
            yield captured
    request_ctx.reset(token)


ORDER_RESPONSE = httpx.Response(
    200,
    json={
        "order_id": "ord-synth-9001",
        "tenant_id": "tenant-a",
        "total": "42.50",
        "status": "paid",
    },
)
REFUND_RESPONSE = httpx.Response(
    200,
    json={
        "order_id": "ord-synth-9001",
        "refund_id": "rfnd-0001",
        "amount": "10.00",
        "reason": "duplicate",
        "status": "refunded",
    },
)
CHAT_RESPONSE = httpx.Response(
    200, json={"text": "hi", "modelId": "m", "inputTokens": 1, "outputTokens": 1}
)


def test_get_order_emits_one_named_span() -> None:
    """``orders.get_order`` produces a single run under its own tool name."""
    with capture_runs(ORDER_RESPONSE) as runs:
        asyncio.run(
            mcp.call_tool(
                "orders.get_order", {"order_id": "ord-synth-9001", "tenant_id": "tenant-a"}
            )
        )
    assert len(runs) == 1
    assert runs[0]["name"] == "orders.get_order"
    assert runs[0]["session_name"] == EXPECTED_PROJECT


def test_create_refund_emits_one_named_span() -> None:
    """``orders.create_refund`` produces a single run under its own tool name."""
    with capture_runs(REFUND_RESPONSE) as runs:
        asyncio.run(
            mcp.call_tool(
                "orders.create_refund",
                {
                    "order_id": "ord-synth-9001",
                    "amount": "10.00",
                    "reason": "duplicate",
                    "tenant_id": "tenant-a",
                    "idempotency_key": "123e4567-e89b-42d3-a456-426614174000",
                },
            )
        )
    assert len(runs) == 1
    assert runs[0]["name"] == "orders.create_refund"
    assert runs[0]["session_name"] == EXPECTED_PROJECT


def test_llm_chat_emits_one_named_span() -> None:
    """``llm.chat`` produces a single run under its own tool name."""
    with capture_runs(CHAT_RESPONSE) as runs:
        asyncio.run(
            mcp.call_tool(
                "llm.chat",
                {
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 16,
                    "tenant_id": "tenant-a",
                },
            )
        )
    assert len(runs) == 1
    assert runs[0]["name"] == "llm.chat"
    assert runs[0]["session_name"] == EXPECTED_PROJECT


def test_rag_emits_one_named_span() -> None:
    """``rag.retrieve_and_generate`` produces a single run under its own tool name."""
    canned = {
        "text": "an answer",
        "citations": [{"chunk_id": "chunk-doc-p1", "chunk_text": "x", "score": 0.5}],
        "coverage": {"jaccard": 0.5},
        "rerank_timed_out": False,
    }
    with capture_runs(ORDER_RESPONSE, rag_result=canned) as runs:
        asyncio.run(
            mcp.call_tool(
                "rag.retrieve_and_generate",
                {"question": "what is the deduction?", "tenant_id": "tenant-a", "top_k": 1},
            )
        )
    assert len(runs) == 1
    assert runs[0]["name"] == "rag.retrieve_and_generate"
    assert runs[0]["session_name"] == EXPECTED_PROJECT


def test_a_failing_tool_still_emits_its_span() -> None:
    """An upstream failure is traced too.

    The span for a call that went wrong is the one an engineer actually goes looking for, so a
    tracing setup that only records successes records the wrong half.
    """
    with (
        capture_runs(httpx.Response(404, json={"error": "nope"})) as runs,
        pytest.raises(Exception, match=r"not found|nope|4040"),
    ):
        asyncio.run(
            mcp.call_tool("orders.get_order", {"order_id": "missing", "tenant_id": "tenant-a"})
        )
    assert len(runs) == 1
    assert runs[0]["name"] == "orders.get_order"
    assert runs[0]["session_name"] == EXPECTED_PROJECT
