# taxcalc-mcp-server/src/taxcalc_mcp_server/tools/__init__.py
"""The four MCP tools and the one read-only resource.

Importing this package is what *registers* them: each module applies ``@mcp.tool`` at import
time against the shared server in :mod:`taxcalc_mcp_server.app`. Both transports therefore
import the modules before calling ``run``, and a transport that forgets to would start a server
publishing an empty tool list - which is why
:func:`taxcalc_mcp_server.app.enforce_strict_tool_schemas` returns a count the entry points
assert on.
"""
