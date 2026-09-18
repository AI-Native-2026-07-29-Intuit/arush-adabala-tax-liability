# taxcalc-mcp-server/tests/test_llm_wire_shapes.py
"""``llm.chat`` speaks both proxy dialects, and the published contract does not move either way.

The W7 D4 brief specifies ``/v1/chat/completions`` with a ``messages`` array. This repo's proxy
is ``llmproxy/LlmProxyController.java``, which serves ``POST /v1/completions`` taking
``{prompt, model, feature}``. Rather than pick one and document the other as unsupported,
:mod:`taxcalc_mcp_server.tools.llm` translates at the boundary and picks the dialect from the
configured path.

That makes two things worth asserting, and neither is visible from the tool schema:

*The right body reaches the right endpoint.* Sending a ``prompt`` field to an endpoint that reads
``messages`` yields an empty completion with a **200** - billed, logged as a success, and wrong.
No error surfaces anywhere, so a test is the only thing that catches it.

*Both replies parse into the same DTO.* A client must not be able to tell which upstream answered
it. These tests read the fields back out of a canned response for each shape and assert the same
four values, which is the actual contract.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import httpx
import pytest
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from mcp.types import TextContent

from taxcalc_mcp_server.app import AppCtx, enforce_strict_tool_schemas, mcp
from taxcalc_mcp_server.settings import Settings
from taxcalc_mcp_server.tools import _resources, llm, orders, rag  # noqa: F401 - registration

#: The two paths that matter: the brief's, and this repo's.
CHAT_PATH = "/v1/chat/completions"
COMPLETIONS_PATH = "/v1/completions"

#: A canned OpenAI-shaped reply, in the shape a generic proxy returns.
CHAT_RESPONSE: dict[str, Any] = {
    "model": "claude-haiku-4-5-20251001",
    "choices": [{"message": {"role": "assistant", "content": "a deduction is a deduction"}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
}

#: The same reply as ``CompletionResponse`` renders it - the Java record's field names, including
#: ``resolvedModel``, which is the dated snapshot and the one a caller can compare across calls.
COMPLETIONS_RESPONSE: dict[str, Any] = {
    "model": "claude-haiku-4-5",
    "resolvedModel": "claude-haiku-4-5-20251001",
    "feature": "mcp.llm.chat",
    "inputTokens": 11,
    "outputTokens": 7,
    "text": "a deduction is a deduction",
}

#: What a client must see regardless of which upstream answered.
EXPECTED_REPLY: dict[str, Any] = {
    "text": "a deduction is a deduction",
    "model": "claude-haiku-4-5-20251001",
    "input_tokens": 11,
    "output_tokens": 7,
}

ARGUMENTS: dict[str, Any] = {
    "messages": [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "what is a deduction?"},
    ],
    "max_tokens": 64,
    "tenant_id": "tenant-a",
}


@pytest.fixture(scope="module", autouse=True)
def _hardened() -> None:
    """Apply the transports' startup hardening once, so dispatch matches the running server."""
    assert enforce_strict_tool_schemas(mcp) == 4


@contextmanager
def dispatch_against(path: str, response: dict[str, Any]) -> Iterator[list[httpx.Request]]:
    """Dispatch ``llm.chat`` against a proxy mounted at ``path``, capturing the outbound request.

    Settings are constructed with an explicit ``llm_proxy_chat_path`` rather than by patching the
    environment, because :class:`Settings` is built at import time - an env var set from a test
    body is a value the settings object was created without.

    :param path: The proxy path to configure, which is what selects the wire shape.
    :param response: The canned upstream JSON body.
    :yields: The list of requests the tool actually sent, populated by the time the block exits.
    """
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=response, headers={"X-Cost-Usd": "0.0042"})

    settings = Settings(llm_proxy_chat_path=path)
    client = httpx.AsyncClient(
        base_url=settings.normalised_orders_url(), transport=httpx.MockTransport(handler)
    )

    def unused_rag(*_: object, **__: object) -> dict[str, Any]:
        raise AssertionError("llm.chat must not touch the retrieval pipeline")

    token = request_ctx.set(
        RequestContext(
            request_id="test",
            meta=None,
            session=None,  # type: ignore[arg-type]  # no path under test touches the session
            lifespan_context=AppCtx(http=client, rag_fn=unused_rag, settings=settings),
        )
    )
    try:
        yield sent
    finally:
        request_ctx.reset(token)


async def _call_chat() -> dict[str, Any]:
    """Invoke ``llm.chat`` through the real MCP dispatch.

    :returns: The decoded JSON payload the tool produced.
    """
    result = cast("tuple[object, object]", await mcp.call_tool("llm.chat", ARGUMENTS))
    blocks = result[0]
    assert isinstance(blocks, list), f"expected content blocks, got {type(blocks).__name__}"
    first = blocks[0]
    assert isinstance(first, TextContent), f"expected a text block, got {type(first).__name__}"
    payload: dict[str, Any] = json.loads(first.text)
    return payload


# ---- Shape selection ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/v1/chat/completions", "chat"),
        ("/v1/chat/completions/", "chat"),
        ("/internal/v1/chat/completions", "chat"),
        ("/v1/completions", "completions"),
        ("/api/v1/completions", "completions"),
    ],
)
def test_the_path_selects_the_dialect(path: str, expected: str) -> None:
    """The shape is derived from the configured path, not carried in a second setting.

    Two settings that must agree are two settings that can disagree, and the disagreement here
    is silent: a ``prompt`` posted to a ``messages`` endpoint returns 200 with no completion.
    """
    assert llm._wire_shape(path) == expected


# ---- The brief's endpoint: /v1/chat/completions ---------------------------------------------


async def test_chat_endpoint_receives_a_real_messages_array() -> None:
    """Against ``/v1/chat/completions`` the turns go out intact, with ``max_tokens``.

    This is the deliverable's stated endpoint, exercised end to end through MCP dispatch rather
    than asserted about in a docstring.
    """
    with dispatch_against(CHAT_PATH, CHAT_RESPONSE) as sent:
        reply = await _call_chat()

    assert len(sent) == 1
    assert sent[0].url.path == CHAT_PATH
    body = json.loads(sent[0].content)
    assert body == {
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "what is a deduction?"},
        ],
        "max_tokens": 64,
    }
    assert "prompt" not in body, "the chat endpoint reads messages; a prompt field is dropped"
    assert reply == EXPECTED_REPLY


async def test_chat_endpoint_forwards_the_jwt_and_the_tenant() -> None:
    """The proxy call carries the bearer token and the tenant header, like every other tool."""
    with dispatch_against(CHAT_PATH, CHAT_RESPONSE) as sent:
        await _call_chat()

    headers = sent[0].headers
    # Compared against the CONFIGURED token rather than against conftest's literal. conftest
    # sets it with `setdefault`, so a developer - or a CI job, which sets it to something else
    # entirely - can arrive with their own value, and a hard-coded literal here would fail on a
    # difference that is not a defect. What is being asserted is that the token in force is the
    # one that leaves the process, not what that token happens to be.
    assert headers["authorization"] == f"Bearer {Settings().bearer_jwt.get_secret_value()}"
    assert headers["x-tenant"] == "tenant-a"


# ---- This repo's endpoint: /v1/completions --------------------------------------------------


async def test_completions_endpoint_receives_the_java_records_three_fields() -> None:
    """Against ``/v1/completions`` the turns are flattened into ``prompt``, tagged by feature.

    ``CompletionRequest`` is a three-field record with no token ceiling, so ``max_tokens`` is
    deliberately NOT forwarded: Spring Boot ignores unknown JSON properties by default, and a
    field that is silently dropped upstream reads, to anyone auditing the body, like a limit
    that is being enforced.
    """
    with dispatch_against(COMPLETIONS_PATH, COMPLETIONS_RESPONSE) as sent:
        reply = await _call_chat()

    assert sent[0].url.path == COMPLETIONS_PATH
    body = json.loads(sent[0].content)
    assert body == {
        "prompt": "system: be terse\n\nuser: what is a deduction?",
        "feature": llm.COST_FEATURE,
    }
    assert "maxTokens" not in body
    assert reply == EXPECTED_REPLY


def test_completions_reply_prefers_the_resolved_model() -> None:
    """``resolvedModel`` wins over ``model``, and a missing one falls back rather than blanking.

    The bare id is what the caller asked for; the resolved dated snapshot is what answers "did
    these two replies come from the same model", which is the only reason the field is published.
    """
    assert llm._parse_reply(COMPLETIONS_RESPONSE, "completions").model == (
        "claude-haiku-4-5-20251001"
    )
    assert llm._parse_reply({"model": "claude-haiku-4-5"}, "completions").model == (
        "claude-haiku-4-5"
    )


# ---- Both dialects, one DTO -----------------------------------------------------------------


def test_both_dialects_parse_into_the_same_reply() -> None:
    """A client cannot tell which upstream answered it. That is the whole contract."""
    assert llm._parse_reply(CHAT_RESPONSE, "chat") == llm._parse_reply(
        COMPLETIONS_RESPONSE, "completions"
    )


@pytest.mark.parametrize(
    ("body", "shape"),
    [
        ({}, "chat"),
        ({"choices": []}, "chat"),
        ({"choices": [{}]}, "chat"),
        ({"choices": "not-a-list"}, "chat"),
        ({"usage": "not-a-dict", "choices": [{"message": {"content": "x"}}]}, "chat"),
        ({}, "completions"),
        ({"inputTokens": "not-a-number"}, "completions"),
    ],
)
def test_a_malformed_200_degrades_instead_of_raising(body: dict[str, Any], shape: str) -> None:
    """A 200 with a body missing the fields must not become a 5030 that hides the answer.

    The caller has already been billed for this call. Losing whatever text did come back - to an
    ``IndexError`` over an absent ``usage`` block - trades a partial answer for no answer.
    """
    reply = llm._parse_reply(body, cast("Any", shape))
    assert isinstance(reply.text, str)
    assert reply.input_tokens >= 0
    assert reply.output_tokens >= 0


def test_token_counts_survive_being_sent_as_strings() -> None:
    """A proxy that renders counts as JSON strings still yields integers, not zeroes."""
    reply = llm._parse_reply(
        {"text": "x", "inputTokens": "11", "outputTokens": "7"}, "completions"
    )
    assert reply.input_tokens == 11
    assert reply.output_tokens == 7
