# taxcalc-mcp-server/tests/conftest.py
"""Shared fixtures and the environment every test in this package needs.

**Why the environment is set at import time and not in a fixture.** Importing
:mod:`taxcalc_mcp_server.app` constructs :class:`Settings`, and importing any tool module
transitively imports ``app``. A fixture runs after collection, which is after those imports have
already happened - so a value a fixture sets is a value the settings object was built without.
Setting them here, before the first import, is the only ordering that works.

The values are obvious non-secrets. ``LANGSMITH_API_KEY`` is required by
:mod:`taxcalc_ai.rag` at import; ``setdefault`` rather than assignment so a developer running
against a real project keeps their own key and their spans keep landing where they expect.
"""

from __future__ import annotations

import os

os.environ.setdefault("LANGSMITH_API_KEY", "test-not-a-real-key")
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("TAXCALC_MCP_BEARER_JWT", "test-bearer-token")
os.environ.setdefault("TAXCALC_MCP_ORDERS_SVC_URL", "http://orders.test")
os.environ.setdefault("TAXCALC_MCP_LLM_PROXY_URL", "http://llm-proxy.test")
