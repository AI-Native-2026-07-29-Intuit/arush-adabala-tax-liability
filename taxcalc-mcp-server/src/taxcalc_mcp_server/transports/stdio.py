# taxcalc-mcp-server/src/taxcalc_mcp_server/transports/stdio.py
"""Stdio entry point, used by Claude Desktop.

**Stdout is the protocol.** Logging is pinned to stderr in :mod:`taxcalc_mcp_server.app`, which
is imported before anything else can print. See that module's docstring for why both the
standard library and ``structlog`` are redirected rather than one of the two.

**The tool imports are the registration.** ``from taxcalc_mcp_server.tools import ...`` looks
like an unused import and is not: applying ``@mcp.tool`` is a side effect of importing the
module, so dropping the import would start a server that speaks the protocol correctly and
publishes nothing. :func:`enforce_strict_tool_schemas` returning zero is the assertion that
catches that mistake at startup rather than at the first ``tools/list``.
"""

from __future__ import annotations

from taxcalc_mcp_server.app import enforce_strict_tool_schemas, log, mcp
from taxcalc_mcp_server.tools import _resources, llm, orders, rag  # noqa: F401 - registration


def main() -> None:
    """Serve MCP over stdio until the client closes the pipe.

    :raises RuntimeError: if no tools were registered, which means the tool modules did not
        import. Failing here is a process that dies at launch with a legible reason, rather than
        one that a user has to diagnose from an empty tool menu in a desktop client.
    """
    registered = enforce_strict_tool_schemas(mcp)
    if registered == 0:
        raise RuntimeError(
            "no MCP tools registered: the tool modules did not import, so this server would "
            "publish an empty tools/list"
        )
    log.info("transport.start", transport="stdio", tools=registered)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
