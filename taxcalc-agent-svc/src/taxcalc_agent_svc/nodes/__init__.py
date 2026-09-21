# taxcalc-agent-svc/src/taxcalc_agent_svc/nodes/__init__.py
"""The three node bodies and the deadline decorator they all wear.

Each node body is small on purpose - the complexity of this deliverable lives in the *topology*
(reducers, a supervisor returning a fan-out plan, deadlines, a checkpointer, two budget caps),
not in the bodies. ``retrieval`` wraps the W7 D3 pipeline, ``api`` wraps the W7 D4 MCP tool
catalogue, and ``synthesis`` wraps one Instructor-typed generation call. A node that grew its own
retrieval or its own HTTP client would be a second copy of something this repository already
ships and already tests.
"""
