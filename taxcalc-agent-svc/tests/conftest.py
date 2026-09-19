# taxcalc-agent-svc/tests/conftest.py
"""Shared fixtures: settings that need no credentials, and stubs for the three dependencies.

The design rule this file enforces is that **a unit test of the graph needs no network, no
database and no API key**. Every one of those is reached through a seam - the MCP session and
budget guard arrive on the config, the retrieval pipeline arrives through
:func:`taxcalc_agent_svc.retrievers.run_pipeline`, and the Anthropic clients are constructed
inside the node bodies from settings that default to an empty key. A test that had to stand up
Postgres to assert "the supervisor routes an order question to the api agent" would be a test
nobody runs locally.
"""

from __future__ import annotations

from typing import Any

import pytest

from taxcalc_agent_svc.budgets import BudgetGuard
from taxcalc_agent_svc.settings import Settings


class StubTool:
    """One entry in a stubbed MCP tool catalogue.

    Mirrors the three attributes :func:`taxcalc_agent_svc.nodes.api.tools_for_claude` reads off a
    real ``mcp.types.Tool`` - deliberately only those three, so a test cannot accidentally depend
    on SDK surface the production code never touches.
    """

    def __init__(self, name: str, description: str, input_schema: dict[str, Any]) -> None:
        """Construct a stub tool.

        :param name: The tool name.
        :param description: Its description.
        :param input_schema: Its published JSON Schema.
        """
        self.name = name
        self.description = description
        # camelCase deliberately: it mirrors the MCP SDK's own field name, and a stub that
        # renamed it would not exercise the production code's attribute access.
        self.inputSchema = input_schema


class StubCatalogue:
    """What a stubbed ``session.list_tools()`` returns.

    :ivar tools: The stub tools.
    """

    def __init__(self, tools: list[StubTool]) -> None:
        """Construct a stub catalogue.

        :param tools: The tools to publish.
        """
        self.tools = tools


class StubToolResult:
    """What a stubbed ``session.call_tool()`` returns.

    :ivar content: The tool's result payload.
    """

    def __init__(self, content: Any) -> None:
        """Construct a stub result.

        :param content: The payload.
        """
        self.content = content


class StubSession:
    """A stand-in for ``mcp.ClientSession`` that records what it was called with.

    Recording rather than asserting: the assertions belong in the tests, and a stub that asserted
    would constrain every test that used it to one expectation. ``calls`` is the evidence; each
    test decides what it means.

    :ivar calls: ``(name, arguments, meta)`` per call, in order.
    """

    def __init__(self, tools: list[StubTool], results: dict[str, Any] | None = None) -> None:
        """Construct a stub session.

        :param tools: The catalogue to publish.
        :param results: Tool name -> canned result payload.
        """
        self._tools = tools
        self._results = results or {}
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []

    async def list_tools(self) -> StubCatalogue:
        """Publish the stub catalogue.

        :returns: The catalogue.
        """
        return StubCatalogue(self._tools)

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, meta: dict[str, Any] | None = None
    ) -> StubToolResult:
        """Record the call and return the canned result.

        The signature mirrors mcp 1.x's ``ClientSession.call_tool`` - notably ``meta`` as a
        keyword-only argument and **no** ``headers`` parameter. That is not incidental: a stub
        that accepted ``headers`` would let the production code keep calling a parameter the real
        SDK does not have, which is exactly the defect this project found in the reference sketch.

        :param name: The tool being called.
        :param arguments: Its arguments.
        :param meta: The MCP ``_meta`` payload.
        :returns: The canned result.
        """
        self.calls.append((name, dict(arguments), meta))
        return StubToolResult(self._results.get(name, {"ok": True}))


@pytest.fixture
def settings() -> Settings:
    """Settings that construct without any credential or reachable service.

    Deadlines are left at their defaults so a test that means to exercise a timeout has to say
    so by overriding one, rather than inheriting a value short enough to make unrelated tests
    flaky on a loaded runner.

    :returns: Validated settings.
    """
    return Settings(
        postgres_url="postgresql://unused:unused@localhost:1/unused",
        langsmith_project="taxcalc-agent-svc-test",
    )


@pytest.fixture
def guard() -> BudgetGuard:
    """A budget guard with the default ceiling.

    :returns: A fresh guard.
    """
    return BudgetGuard()


@pytest.fixture
def four_tools() -> list[StubTool]:
    """The W7 D4 catalogue's published shape, as verified against the live server.

    Every tool declares ``tenant_id``; exactly one declares ``idempotency_key``. Those two facts
    are what :func:`taxcalc_agent_svc.nodes.api.inject_context` keys off, so they are reproduced
    here rather than invented - a fixture that declared ``idempotency_key`` on every tool would
    let a bug in the schema-driven injection pass.

    :returns: Four stub tools.
    """

    def schema(*props: str) -> dict[str, Any]:
        return {"type": "object", "properties": {p: {"type": "string"} for p in props}}

    return [
        StubTool("orders.get_order", "Fetch one order.", schema("order_id", "tenant_id")),
        StubTool(
            "orders.create_refund",
            "Refund an order, idempotently.",
            schema("order_id", "amount", "reason", "tenant_id", "idempotency_key"),
        ),
        StubTool(
            "llm.chat", "Chat through the proxy.", schema("messages", "max_tokens", "tenant_id")
        ),
        StubTool(
            "rag.retrieve_and_generate",
            "Answer from the corpus.",
            schema("question", "tenant_id", "top_k"),
        ),
    ]
