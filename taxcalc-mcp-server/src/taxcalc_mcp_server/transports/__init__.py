# taxcalc-mcp-server/src/taxcalc_mcp_server/transports/__init__.py
"""The two entry points that serve the same handlers over different wires.

Both are thin by design. ``stdio`` is what Claude Desktop launches as a subprocess; ``sse`` is
what the W7 D5 agent connects to over HTTP inside the cluster. Neither contains tool logic, and
neither may: a rule enforced in one transport and not the other is a rule that holds for desktop
users and not for the agent, which is the worst possible place for a security boundary to differ.
"""
