"""taxcalc-ai - the Python sidecar that owns the AI/ML half of the taxcalc stack.

The Java service (``taxcalc-api``) keeps the transactional Postgres workload and the
latency-sensitive HTTP surface. This package owns the calls that go out to the W3 D1 LLM
proxy, the Pydantic boundary models that round-trip the Java service's JSON, and the
internal value types those calls are built from.

Module map:

* :mod:`taxcalc_ai.models` - Pydantic v2 boundary models (validated, ``extra="forbid"``).
* :mod:`taxcalc_ai.value_types` - frozen dataclasses for purely internal values.
* :mod:`taxcalc_ai.settings` - 12-factor configuration, secrets held in ``SecretStr``.
* :mod:`taxcalc_ai.client` - the httpx client that calls the proxy, with retries and logs.
* :mod:`taxcalc_ai.cli` - the one module allowed to write to stdout.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
