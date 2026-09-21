# taxcalc-agent-svc/src/taxcalc_agent_svc/__init__.py
"""The W7 D5 multi-agent capstone: a three-node LangGraph behind one FastAPI service.

The package is deliberately thin at the top. Everything a caller needs is reached through
:mod:`taxcalc_agent_svc.app` (the HTTP surface and the lifespan that owns the shared clients) or
:mod:`taxcalc_agent_svc.graph` (the compiled graph), and nothing is constructed at import time -
importing this package opens no socket, loads no model and reads no credential.
"""

#: Published in the FastAPI OpenAPI document and in the ``/healthz`` payload, so an operator
#: looking at a running pod can tell which build answered without shelling into it.
__version__ = "0.1.0"
