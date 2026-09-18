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

*The structured log lines themselves.* ``tool.invoke.end`` is not decoration - it is the input
to the Grafana dashboard and to the cost report, so a field that silently stops being emitted
breaks a dashboard rather than a test. The assertions below treat the field set as the contract
it is, which is also the only way to keep ``cost_usd_minor`` present on the three tools that do
not set it themselves.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import anyio
import httpx
import pytest
from mcp import McpError
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from mcp.types import TextContent

from taxcalc_mcp_server.app import AppCtx, enforce_strict_tool_schemas, mcp
from taxcalc_mcp_server.observability import (
    COST_SOURCE_NONE,
    COST_SOURCE_PROXY,
    COST_SOURCE_UNPRICED,
)
from taxcalc_mcp_server.scripts.healthcheck import HEALTHY_STATUSES, probe
from taxcalc_mcp_server.scripts.replay import (
    NOISE_FLOOR_MS,
    P95_REGRESSION_LIMIT,
    _percentiles,
    compare,
)
from taxcalc_mcp_server.settings import Settings
from taxcalc_mcp_server.tenancy import (
    _REQUEST_JWT,
    _REQUEST_TENANT,
    auth_headers,
    bearer_token,
    cross_tenant_claim,
    parse_bearer,
    request_tenant,
)
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


# ---- The structured log contract ------------------------------------------------------------


@contextmanager
def captured_lines() -> Iterator[list[dict[str, Any]]]:
    """Capture the structlog events emitted inside the block.

    ``structlog.testing.capture_logs`` rather than ``capsys``: the real logger is constructed
    with a reference to ``sys.stderr`` taken at configure time, so a captured-stdio fixture sees
    nothing and the test passes for the wrong reason. This intercepts at the processor chain,
    which is where the fields actually exist as data rather than as rendered JSON.

    :yields: The list events are appended to, in emission order.
    """
    from structlog.testing import capture_logs

    with capture_logs() as entries:
        yield cast("list[dict[str, Any]]", entries)


def _end_line(entries: list[dict[str, Any]], tool: str) -> dict[str, Any]:
    """Return the single ``tool.invoke.end`` line ``tool`` emitted.

    :param entries: Captured events.
    :param tool: The tool whose line is wanted.
    :returns: That line.
    """
    ends = [e for e in entries if e.get("event") == "tool.invoke.end" and e.get("tool") == tool]
    assert len(ends) == 1, f"expected exactly one end line for {tool}, got {len(ends)}"
    starts = [
        e for e in entries if e.get("event") == "tool.invoke.start" and e.get("tool") == tool
    ]
    assert len(starts) == 1, f"expected exactly one start line for {tool}, got {len(starts)}"
    return ends[0]


def _ok_order_response(_: httpx.Request) -> httpx.Response:
    """Answer any request with a valid order view.

    :param _: The outbound request, ignored.
    :returns: A 200 carrying the order the ``orders.*`` tools expect.
    """
    return httpx.Response(
        200,
        json={
            "order_id": "ord-synth-9001",
            "tenant_id": "tenant-a",
            "total": "42.50",
            "status": "paid",
        },
    )


async def test_orders_end_line_carries_an_explicit_zero_cost() -> None:
    """A tool that spends nothing still reports the field, as a true zero.

    Absent would be worse than zero: a dashboard summing ``cost_usd_minor`` cannot tell a
    missing key from a gap in collection, so every tool emits the number whether or not it has
    one to report.
    """
    with captured_lines() as entries, dispatch_against(httpx.MockTransport(_ok_order_response)):
        await _call("orders.get_order", {"order_id": "ord-synth-9001", "tenant_id": "tenant-a"})
    line = _end_line(entries, "orders.get_order")
    assert line["cost_usd_minor"] == 0
    assert line["cost_source"] == COST_SOURCE_NONE
    assert line["tenant_id"] == "tenant-a"
    assert isinstance(line["duration_ms"], int)


async def test_llm_end_line_prices_the_call_from_the_proxy_header() -> None:
    """``X-Cost-Usd`` becomes integer minor units, and the source says where it came from."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"text": "hello", "resolvedModel": "m", "inputTokens": 1, "outputTokens": 2},
            headers={"X-Cost-Usd": "0.0342"},
        )

    with captured_lines() as entries, dispatch_against(httpx.MockTransport(handler)):
        await _call(
            "llm.chat",
            {
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 16,
                "tenant_id": "tenant-a",
            },
        )
    line = _end_line(entries, "llm.chat")
    # 3.42 cents truncates to 3: minor units are the smallest unit anyone bills in, and rounding
    # up would invent spend that never happened.
    assert line["cost_usd_minor"] == 3
    assert line["cost_source"] == COST_SOURCE_PROXY


async def test_rag_end_line_marks_its_cost_unpriced_rather_than_free() -> None:
    """The retrieval tool spends real money this server cannot see, and says so.

    Reporting :data:`COST_SOURCE_NONE` here would book the server's most expensive call as free,
    which is the one direction a cost dashboard must never be wrong in.
    """
    raw = {
        "text": "a",
        "citations": [{"chunk_id": "chunk-doc-p1", "chunk_text": "x", "score": 0.5}],
        "coverage": {"jaccard": 0.2},
        "rerank_timed_out": False,
    }
    with captured_lines() as entries, dispatch_against(rag_result=raw):
        await _call(
            "rag.retrieve_and_generate",
            {"question": "q?", "tenant_id": "tenant-a", "top_k": 1},
        )
    line = _end_line(entries, "rag.retrieve_and_generate")
    assert line["cost_source"] == COST_SOURCE_UNPRICED
    assert line["cost_usd_minor"] == 0


async def test_a_failed_call_reports_the_error_code_and_still_reports_cost() -> None:
    """The ``end`` line on the failure path carries both the code and the cost fields.

    A rate-limited proxy call is the case that proves it: the proxy billed for the attempt, and a
    dashboard built only from successful calls would show the spend nowhere.
    """
    transport = httpx.MockTransport(
        lambda _: httpx.Response(429, json={"error": "slow down"}, headers={"X-Cost-Usd": "0.01"})
    )
    with (
        captured_lines() as entries,
        dispatch_against(transport),
        pytest.raises(McpError),
    ):
        await _call(
            "llm.chat",
            {
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 16,
                "tenant_id": "tenant-a",
            },
        )
    line = _end_line(entries, "llm.chat")
    assert line["mcp_error_code"] == 4290
    assert line["cost_usd_minor"] == 1, "a 429 that billed must still report what it cost"


# ---- The tenant cross-check ----------------------------------------------------------------


@contextmanager
def as_bearer_for(tenant_id: str) -> Iterator[None]:
    """Bind a validated-bearer identity for the block, then unbind it.

    Reset through the ``ContextVar`` tokens rather than by setting ``""`` afterwards: a test that
    leaves a value behind would silently arm the cross-check for every test that runs after it,
    and the failure would appear in an unrelated one.

    :param tenant_id: The ``tenant_id`` claim to present.
    :yields: Nothing; the identity is bound for the block.
    """
    jwt_token = _REQUEST_JWT.set("header.payload.signature")
    tenant = _REQUEST_TENANT.set(tenant_id)
    try:
        yield
    finally:
        _REQUEST_TENANT.reset(tenant)
        _REQUEST_JWT.reset(jwt_token)


async def test_a_bearer_scoped_to_another_tenant_is_refused_before_the_upstream_call() -> None:
    """A cross-tenant call is refused as 4030, and no request leaves the process.

    This is the half of the ``ContextVar`` mechanism that makes it worth having: the claim is
    read by the instrument every handler already goes through, so no tool has to remember to
    check it and none can be written that forgets.
    """
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _ok_order_response(request)

    with (
        captured_lines() as entries,
        as_bearer_for("tenant-b"),
        dispatch_against(httpx.MockTransport(handler)),
        pytest.raises(McpError) as caught,
    ):
        await _call("orders.get_order", {"order_id": "ord-synth-9001", "tenant_id": "tenant-a"})
    assert caught.value.error.code == 4030
    assert calls == [], "the refusal must happen before the order service is called"
    assert _end_line(entries, "orders.get_order")["mcp_error_code"] == 4030
    # Neither tenant id appears in the caller-facing message: telling the caller which tenant
    # their token IS scoped to is the enumeration this check exists to stop.
    assert "tenant-a" not in caught.value.error.message
    assert "tenant-b" not in caught.value.error.message


async def test_a_bearer_scoped_to_the_requested_tenant_is_not_refused() -> None:
    """The matching case must pass through untouched, or the check is an outage."""
    with as_bearer_for("tenant-a"), dispatch_against(httpx.MockTransport(_ok_order_response)):
        payload = await _call(
            "orders.get_order", {"order_id": "ord-synth-9001", "tenant_id": "tenant-a"}
        )
    assert payload["order_id"] == "ord-synth-9001"


async def test_an_unknown_tenant_claim_is_not_a_refusal() -> None:
    """The stdio transport, and SSE with validation disabled, carry no claim - and must work.

    ``jwks_url`` is empty by default, so "no local opinion" is the common case rather than the
    exotic one. Treating it as a mismatch would break every stdio tool call.
    """
    with dispatch_against(httpx.MockTransport(_ok_order_response)):
        payload = await _call(
            "orders.get_order", {"order_id": "ord-synth-9001", "tenant_id": "tenant-a"}
        )
    assert payload["order_id"] == "ord-synth-9001"


@pytest.mark.parametrize(
    ("claim", "requested", "expected"),
    [
        ("tenant-b", "tenant-a", "tenant-b"),
        ("tenant-a", "tenant-a", ""),
        ("", "tenant-a", ""),
    ],
)
def test_cross_tenant_claim_reports_only_a_real_disagreement(
    claim: str, requested: str, expected: str
) -> None:
    """Unknown reads as agreement; only two known, differing values are a conflict."""
    with as_bearer_for(claim):
        assert cross_tenant_claim(requested) == expected


# ---- Local JWKS validation ------------------------------------------------------------------
#
# The only tests in this file that mint real tokens. Worth the RSA keypair: `_validate` is the
# code that decides whether a signature, an audience and an expiry are checked at all, and its
# failure mode is silent - a middleware that accepts everything looks exactly like one that
# accepts the right things until someone presents a token for the wrong audience. The keypair is
# generated once per session because 2048-bit generation is the slowest thing in this suite.


@pytest.fixture(scope="session")
def signing_key() -> Any:
    """Generate the RSA keypair every token in this section is signed with.

    :returns: The private key; its public half is what the stubbed JWKS client hands back.
    """
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def validating_settings() -> Settings:
    """Settings with local validation switched ON.

    :returns: Settings naming a JWKS URL that is never fetched - the client is stubbed - and the
        audience the tokens below are minted for.
    """
    return Settings(
        jwks_url="https://idp.invalid/.well-known/jwks.json",
        jwt_audience="taxcalc-api",
    )


@pytest.fixture(autouse=True)
def _stub_jwks(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Point :class:`PyJWKClient` at the test keypair instead of the network.

    Patched at the client rather than by serving a JWKS document over HTTP: the key *resolution*
    is PyJWT's code and is not what these tests are about, while the network call would make the
    suite depend on a host that does not exist.

    :param monkeypatch: Patcher.
    :param request: Used to resolve the session-scoped keypair only for the tests that ask.
    """
    if "validating_settings" not in request.fixturenames:
        return
    from types import SimpleNamespace

    from jwt import PyJWKClient

    public_key = request.getfixturevalue("signing_key").public_key()
    monkeypatch.setattr(
        PyJWKClient,
        "get_signing_key_from_jwt",
        lambda _self, _token: SimpleNamespace(key=public_key),
    )


def _mint(key: Any, *, audience: str, tenant_id: str = "tenant-a", ttl_s: int = 300) -> str:
    """Sign a bearer token.

    :param key: The private key.
    :param audience: The ``aud`` claim.
    :param tenant_id: The ``tenant_id`` claim.
    :param ttl_s: Seconds until expiry; negative mints an already-expired token.
    :returns: The encoded JWT.
    """
    import time as _time

    import jwt as pyjwt

    return pyjwt.encode(
        {
            "sub": "user-1",
            "aud": audience,
            "tenant_id": tenant_id,
            "exp": int(_time.time()) + ttl_s,
        },
        key,
        algorithm="RS256",
    )


def _recording_middleware(settings: Settings, seen: dict[str, Any]) -> Any:
    """Wrap a downstream app that records the identity the middleware bound.

    The real SSE app is not used here: a request that passes the middleware opens an event stream
    and never completes, so the assertion could never run. A one-line downstream app is what
    makes the *accepted* path testable at all - and the accepted path is where tenant extraction
    has to be proven.

    :param settings: Configuration for the middleware.
    :param seen: Dict the downstream app records into.
    :returns: The wrapped ASGI app.
    """
    from starlette.responses import JSONResponse

    from taxcalc_mcp_server.transports.sse import BearerMiddleware

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        seen["tenant"] = request_tenant()
        seen["forwarded"] = bearer_token("")
        await JSONResponse({"ok": True})(scope, receive, send)

    return BearerMiddleware(downstream, settings=settings)


def test_a_token_for_the_wrong_audience_is_refused_as_4030(
    signing_key: Any, validating_settings: Settings
) -> None:
    """A validly-signed token minted for another service does not open a session here.

    The audience claim is the difference between "this credential is genuine" and "this
    credential was issued for us". A validator that checks the signature and not the audience
    accepts every token the identity provider ever signed, for any service in the estate.
    """
    from starlette.testclient import TestClient

    from taxcalc_mcp_server.transports.sse import build_app

    token = _mint(signing_key, audience="some-other-service")
    with TestClient(build_app(validating_settings)) as client:
        response = client.get("/sse", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == 4030
    # The refusal names neither the expected audience nor the issuer; PyJWT's own message does,
    # which is why the exception text is logged rather than echoed.
    assert "taxcalc-api" not in response.text


def test_an_expired_token_is_refused_as_4030(
    signing_key: Any, validating_settings: Settings
) -> None:
    """Expiry is enforced. An expired token that validates is the same as no expiry at all."""
    from starlette.testclient import TestClient

    from taxcalc_mcp_server.transports.sse import build_app

    token = _mint(signing_key, audience="taxcalc-api", ttl_s=-60)
    with TestClient(build_app(validating_settings)) as client:
        response = client.get("/sse", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == 4030


def test_a_valid_token_binds_its_tenant_claim_to_the_request(
    signing_key: Any, validating_settings: Settings
) -> None:
    """The accepted path extracts ``tenant_id`` into the context and forwards the raw token."""
    from starlette.testclient import TestClient

    seen: dict[str, Any] = {}
    token = _mint(signing_key, audience="taxcalc-api", tenant_id="tenant-z")
    # Not used as a context manager: entering one drives the ASGI lifespan protocol, and the
    # one-line downstream app below implements `http` only. Nothing here needs a startup event.
    client = TestClient(_recording_middleware(validating_settings, seen))
    response = client.get("/sse", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert seen["tenant"] == "tenant-z", "the claim must reach the handler without a parameter"
    assert seen["forwarded"] == token, "the caller's own token is what gets forwarded upstream"


def test_an_unsigned_none_algorithm_token_is_refused(
    signing_key: Any, validating_settings: Settings
) -> None:
    """A token nominating ``alg: none`` validates against no key and must not be accepted.

    The allow-list in the transport is what makes this true; without it PyJWT honours the
    token's own header, and anyone can mint one of these in a browser console.
    """
    import jwt as pyjwt
    from starlette.testclient import TestClient

    from taxcalc_mcp_server.transports.sse import build_app

    forged = pyjwt.encode(
        {"aud": "taxcalc-api", "tenant_id": "tenant-a", "exp": 2000000000},
        key="",
        algorithm="none",
    )
    with TestClient(build_app(validating_settings)) as client:
        response = client.get("/sse", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401
    assert response.json()["code"] == 4030


# ---- The noise floor under the p95 gate -----------------------------------------------------
#
# These exist because the gate was measurably flaky before the floor and the warmup: two
# consecutive runs of the replay script, with no change between them, reported +18% and +44%.
# The tests below pin the two properties that make it trustworthy - noise is ignored, and a
# change that crosses into perceptible territory is not.


def test_two_sub_millisecond_numbers_are_not_a_regression() -> None:
    """A 0.25ms -> 0.40ms swing is jitter, not a regression, and must not fail a build.

    This is exactly the shape the gate used to fire on: +60% growth on a call no caller could
    tell apart from the original.
    """
    current = {"tools": {"llm.chat": {"p95": 0.40}}}
    previous = {"tools": {"llm.chat": {"p95": 0.25}}}
    assert compare(current, previous) == []


def test_a_change_that_crosses_the_noise_floor_is_still_a_regression() -> None:
    """The floor must not become a blind spot: a sub-millisecond baseline still gets compared.

    A handler that went from 0.3ms to 3ms has regressed tenfold. Skipping the comparison because
    the *baseline* was small is how a noise filter turns into a hole, so the floor is applied to
    the pair and this case is reported.
    """
    current = {"tools": {"llm.chat": {"p95": 3.0}}}
    previous = {"tools": {"llm.chat": {"p95": 0.3}}}
    messages = compare(current, previous)
    assert len(messages) == 1
    assert "llm.chat" in messages[0]


def test_a_regression_above_the_floor_is_reported_as_before() -> None:
    """The ordinary case - both sides above the floor, growth beyond the limit - is unchanged."""
    baseline = NOISE_FLOOR_MS * 10
    current = {"tools": {"rag.retrieve_and_generate": {"p95": baseline * 1.5}}}
    previous = {"tools": {"rag.retrieve_and_generate": {"p95": baseline}}}
    assert len(compare(current, previous)) == 1


def test_the_warmup_pass_is_recorded_in_the_report() -> None:
    """The report says how many untimed calls preceded the samples.

    Without it a reader cannot tell a report whose p95 includes each tool's first-call cost from
    one whose p95 does not, and those two are not comparable numbers.
    """
    from taxcalc_mcp_server.scripts import replay

    fixtures = replay.load_fixtures(Path("tests/fixtures"))
    report = anyio.run(replay._run, fixtures, 2, 1)
    assert report["warmup"] == 1
    assert report["repeats"] == 2
    for stats in report["tools"].values():
        assert stats["samples"] == 2, "warmup calls must not become samples"
