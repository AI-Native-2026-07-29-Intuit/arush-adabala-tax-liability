# taxcalc-mcp-server/src/taxcalc_mcp_server/__init__.py
"""MCP server publishing the capstone's order, chat and retrieval surfaces as four tools.

The package is deliberately thin. It owns no business logic: every tool is an adapter that
validates its arguments, forwards them to a service that already exists (the W3 D1 Spring
services, or the W7 D3 in-process RAG pipeline), and re-shapes the reply into a small DTO. The
value it adds is the *contract* - transports, schemas, error codes, idempotency, tracing - which
downstream LLM clients code against.
"""

#: Server version. Kept in lockstep with ``pyproject.toml`` and the committed ``mcp.json``; the
#: registration file is what the W7 D5 agent reads at startup, so a drift here is a drift there.
__version__ = "0.1.0"
