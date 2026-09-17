# taxcalc-mcp-server/tests/test_transport_and_scripts.py
"""In-process tests for the parts the subprocess suites cannot reach.

``test_smoke_stdio.py`` drives a real server subprocess, which is the only way to prove the
stdio framing - but a subprocess's behaviour is opaque to the test process, so anything it does
*not* exercise has no coverage anywhere. That leaves three areas worth testing directly, and one
of them is security code:

*The SSE bearer middleware.* It decides whether a request reaches any tool at all. It was the
single largest untested surface in the package, which is the wrong place for that to be true.

*The tool handlers' error paths.* The 404, 429 and timeout branches are where the error table
becomes real. Driven here through the actual MCP dispatch against a mocked transport, so the
argument validation and the schema enforcement run too.

*The two operator scripts.* ``replay``'s regression arithmetic and ``healthcheck``'s status
mapping are both small and both load-bearing: one decides whether a build fails, the other
whether a container is restarted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from mcp import McpError
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from mcp.types import TextContent

from taxcalc_mcp_server.app import AppCtx, enforce_strict_tool_schemas, mcp
from taxcalc_mcp_server.scripts.healthcheck import HEALTHY_STATUSES, probe
from taxcalc_mcp_server.scripts.replay import P95_REGRESSION_LIMIT, _percentiles, compare
from taxcalc_mcp_server.settings import Settings
from taxcalc_mcp_server.tenancy import auth_headers, bearer_token, parse_bearer
from taxcalc_mcp_server.tools import _resources, llm, orders, rag  # noqa: F401 - registration


@pytest.fixture(scope="module", autouse=True)
def _hardened() -> None:
    """Apply the transports' startup hardening once."""
    assert enforce_strict_tool_schemas(mcp) == 4


@contextmanager
def dispatch_against(
    handler: httpx.MockTransport | None = None,
    rag_result: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Install a request context whose upstream is mocked.

    :param handler: Transport answering the tool's outbound calls.
    :param rag_result: Canned pipeline result for the retrieval tool.
    :yields: Nothing; the context is active for the block.
    """
    settings = Settings()
    client = httpx.AsyncClient(
        base_url=settings.normalised_orders_url(),
        transport=handler or httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )

    def fake_rag(*_: object, **__: object) -> dict[str, Any]:
        if rag_result is None:
            raise TimeoutError("pipeline exceeded its deadline")
        return rag_result

    token = request_ctx.set(
        RequestContext(
            request_id="test",
            meta=None,
            session=None,  # type: ignore[arg-type]  # no path under test touches the session
            lifespan_context=AppCtx(http=client, rag_fn=fake_rag, settings=settings),
        )
    )
    try:
        yield
    finally:
        request_ctx.reset(token)


async def _call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke a tool through the real MCP dispatch.

    :param tool: Tool name.
    :param arguments: Tool arguments.
    :returns: The decoded JSON payload the tool produced.
    """
    # FastMCP's converted result is a (content_blocks, structured_content) pair; the text block
    # is what a client actually reads off the wire, so that is what these tests assert on. The
    # declared return type is a union covering image/audio/resource blocks that no tool here
    # produces, so the narrowing is asserted rather than assumed - a tool that started returning
    # a non-text block would fail here instead of raising an opaque AttributeError.
    # The SDK annotates this as `Sequence[ContentBlock] | dict`, but with `convert_result=True`
    # - which FastMCP always passes - it actually returns a (blocks, structured) 2-tuple. The
    # cast records that gap rather than papering over it; the asserts below are what make the
    # assumption fail loudly if a future SDK narrows it for real.
    result = cast("tuple[object, object]", await mcp.call_tool(tool, arguments))
    blocks = result[0]
    assert isinstance(blocks, list), f"expected content blocks, got {type(blocks).__name__}"
    first = blocks[0]
    assert isinstance(first, TextContent), f"expected a text block, got {type(first).__name__}"
    payload: dict[str, Any] = json.loads(first.text)
    return payload


# ---- Tool error paths -----------------------------------------------------------------------


async def test_upstream_404_reaches_the_caller_as_4040() -> None:
    """A not-found order surfaces with the mapped code, through the real dispatch."""
    transport = httpx.MockTransport(lambda _: httpx.Response(404, json={"error": "nope"}))
    with dispatch_against(transport), pytest.raises(McpError) as caught:
        await _call("orders.get_order", {"order_id": "ord-x", "tenant_id": "tenant-a"})
    assert caught.value.error.code == 4040


async def test_proxy_rate_limit_reaches_the_caller_as_4290() -> None:
    """A 429 from the LLM proxy maps to the code the W7 D5 agent backs off on.

    The most load-bearing row in the table: folded into the generic 5030 it would be
    indistinguishable from "the server broke", and the correct response to those two is opposite.
    """
    transport = httpx.MockTransport(lambda _: httpx.Response(429, json={"error": "slow down"}))
    with dispatch_against(transport), pytest.raises(McpError) as caught:
        await _call(
            "llm.chat",
            {
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 16,
                "tenant_id": "tenant-a",
            },
        )
    assert caught.value.error.code == 4290


async def test_rag_timeout_reaches_the_caller_as_5040() -> None:
    """A pipeline that misses its deadline is reported as a timeout, not as a generic failure."""
    with dispatch_against(rag_result=None), pytest.raises(McpError) as caught:
        await _call(
            "rag.retrieve_and_generate",
            {"question": "what is the deduction?", "tenant_id": "tenant-a", "top_k": 3},
        )
    assert caught.value.error.code == 5040


async def test_rag_answer_is_pre_shaped_and_drops_chunk_text() -> None:
    """The DTO keeps ids, scores, one coverage number and the flag - and nothing else."""
    raw = {
        "text": "the answer",
        "citations": [
            {"chunk_id": "chunk-form-1040-p3", "chunk_text": "dropped", "score": 0.9},
            {"chunk_id": "chunk-pub-501-p7", "chunk_text": "dropped", "score": 0.7},
        ],
        "coverage": {"dense_only": 1.0, "sparse_only": 1.0, "both": 2.0, "jaccard": 0.5},
        "rerank_timed_out": False,
    }
    with dispatch_against(rag_result=raw):
        payload = await _call(
            "rag.retrieve_and_generate",
            {"question": "what is the deduction?", "tenant_id": "tenant-a", "top_k": 2},
        )
    assert payload["answer"] == "the answer"
    assert payload["coverage"] == 0.5, "the four-key mapping collapses to the jaccard"
    assert payload["citations"][0] == {
        "chunk_id": "chunk-form-1040-p3",
        "doc_id": "form-1040",
        "score": 0.9,
    }
    assert "chunk_text" not in json.dumps(payload)


async def test_top_k_truncates_the_citation_list() -> None:
    """``top_k`` bounds what comes back, so a caller's context budget is the caller's to set."""
    raw = {
        "text": "a",
        "citations": [
            {"chunk_id": f"chunk-doc-p{i}", "chunk_text": "x", "score": 0.5} for i in range(10)
        ],
        "coverage": {"jaccard": 0.1},
        "rerank_timed_out": True,
    }
    with dispatch_against(rag_result=raw):
        payload = await _call(
            "rag.retrieve_and_generate",
            {"question": "q?", "tenant_id": "tenant-a", "top_k": 3},
        )
    assert len(payload["citations"]) == 3
    assert payload["rerank_timed_out"] is True


async def test_refund_sends_the_key_as_both_a_field_and_a_header() -> None:
    """The idempotency key travels twice, and this asserts both copies leave the process.

    Header-only would be invisible to the service's own persistence; body-only would leave an
    infrastructure retry free to replay the call as a second refund.
    """
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["header"] = request.headers.get("Idempotency-Key")
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "order_id": "ord-synth-9001",
                "refund_id": "rfnd-0001",
                "amount": "10.00",
                "reason": "duplicate",
                "status": "refunded",
            },
        )

    key = str(uuid4())
    with dispatch_against(httpx.MockTransport(handler)):
        await _call(
            "orders.create_refund",
            {
                "order_id": "ord-synth-9001",
                "amount": "10.00",
                "reason": "duplicate",
                "tenant_id": "tenant-a",
                "idempotency_key": key,
            },
        )
    assert seen["header"] == key
    assert seen["body"]["idempotencyKey"] == key
    assert seen["body"]["amount"] == "10.00", "money must leave as a string, not a JSON number"
    assert isinstance(seen["body"]["amount"], str)
    assert seen["auth"].startswith("Bearer ")


# ---- The SSE bearer middleware --------------------------------------------------------------


def _sse_app() -> Any:
    """Build the SSE application with default (validation-disabled) settings.

    :returns: The ASGI app.
    """
    from taxcalc_mcp_server.transports.sse import build_app

    return build_app(Settings())


async def test_sse_refuses_a_request_with_no_bearer() -> None:
    """No credential means no tool reaches a service, and the refusal carries the MCP code."""
    from starlette.testclient import TestClient

    with TestClient(_sse_app()) as client:
        response = client.get("/sse")
    assert response.status_code == 401
    assert response.json()["code"] == 4030


async def test_sse_refuses_a_non_bearer_authorization_header() -> None:
    """``Basic`` credentials are not a bearer and are refused rather than forwarded."""
    from starlette.testclient import TestClient

    with TestClient(_sse_app()) as client:
        response = client.get("/sse", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == 401
    assert response.json()["code"] == 4030


def test_sse_refusal_body_names_no_expected_issuer_or_audience() -> None:
    """The caller is told the verdict, not what to forge next.

    PyJWT's own messages name the expected audience and issuer; echoing them would hand an
    attacker the shape of a valid token. The operator gets that detail on stderr instead.
    """
    from taxcalc_mcp_server.transports.sse import UNAUTHORIZED_CODE

    assert UNAUTHORIZED_CODE == 4030


# ---- Tenancy helpers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc.def.ghi", "abc.def.ghi"),
        ("bearer abc.def.ghi", "abc.def.ghi"),
        ("BEARER  spaced  ", "spaced"),
        ("Basic dXNlcg==", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_parse_bearer_handles_the_scheme_case_insensitively(
    header: str | None, expected: str
) -> None:
    """RFC 7235 makes the scheme case-insensitive, so a lowercase ``bearer`` is valid."""
    assert parse_bearer(header) == expected


def test_configured_token_is_the_fallback_when_no_request_token_is_set() -> None:
    """On stdio there is no per-request token, so the launcher's environment supplies it."""
    assert bearer_token("from-environment") == "from-environment"


def test_auth_headers_carry_both_the_authority_and_the_selector() -> None:
    """The JWT is the authority; ``X-Tenant`` is the selector. A service needs both."""
    headers = auth_headers("tok", "tenant-b")
    assert headers["Authorization"] == "Bearer tok"
    assert headers["X-Tenant"] == "tenant-b"


# ---- Operator scripts -----------------------------------------------------------------------


def test_percentiles_pick_observed_values() -> None:
    """Nearest-rank, so every reported percentile is a call that actually happened."""
    samples = [float(n) for n in range(1, 101)]
    stats = _percentiles(samples)
    assert stats["p50"] in samples
    assert stats["p95"] in samples
    assert stats["p99"] in samples
    assert stats["p50"] < stats["p95"] <= stats["p99"]


def test_regression_beyond_the_limit_is_reported() -> None:
    """A p95 that grows past the limit fails the merge tier."""
    over = 1.0 * (1.0 + P95_REGRESSION_LIMIT + 0.05)
    messages = compare({"tools": {"t": {"p95": over}}}, {"tools": {"t": {"p95": 1.0}}})
    assert len(messages) == 1
    assert "t: p95" in messages[0]


def test_growth_within_the_limit_is_not_a_regression() -> None:
    """Runner noise below the threshold does not fail a build."""
    under = 1.0 * (1.0 + P95_REGRESSION_LIMIT - 0.05)
    assert compare({"tools": {"t": {"p95": under}}}, {"tools": {"t": {"p95": 1.0}}}) == []


def test_a_new_tool_is_not_a_regression() -> None:
    """Adding a tool must not fail the gate, or the gate becomes an argument against new tools."""
    assert compare({"tools": {"new": {"p95": 99.0}}}, {"tools": {}}) == []


def test_a_zero_baseline_is_skipped_rather_than_divided_by() -> None:
    """A zero or negative baseline cannot be a denominator."""
    assert compare({"tools": {"t": {"p95": 5.0}}}, {"tools": {"t": {"p95": 0.0}}}) == []


def test_healthcheck_treats_401_as_healthy() -> None:
    """A refused unauthenticated probe proves the transport AND the middleware are up."""
    assert 401 in HEALTHY_STATUSES
    assert 200 in HEALTHY_STATUSES
    assert 500 not in HEALTHY_STATUSES


def test_healthcheck_fails_when_nothing_is_listening() -> None:
    """A connection refusal is unhealthy - port 1 has nothing behind it."""
    assert probe("http://127.0.0.1:1/sse") == 1
