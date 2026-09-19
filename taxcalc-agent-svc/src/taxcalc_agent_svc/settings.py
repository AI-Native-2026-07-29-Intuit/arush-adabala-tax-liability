# taxcalc-agent-svc/src/taxcalc_agent_svc/settings.py
"""All env-driven config in one place; prefix ``TAXCALC_AGENT_``.

Same consolidation the sidecar and the MCP server use, and for the same reason: a missing or
malformed value fails the process at boot with a ``ValidationError`` naming the field, rather
than failing the first request that happens to need it. For a service whose first request arrives
through a streaming SSE endpoint, that difference is the difference between an unstartable pod -
which a readiness probe catches and a rollout halts - and a pod that serves 200s whose bodies are
error events.

``extra="ignore"`` for the same reason as the MCP server: this process is launched by Kubernetes,
which injects environment variables it has no opinion about, and ``forbid`` would turn an
unrelated operator-set variable into a pod that will not start. Strictness belongs on the request
models, where an unexpected key means a caller sent something wrong.

The two secrets are :class:`~pydantic.SecretStr`, so they render as ``**********`` in ``repr()``,
``str()`` and ``model_dump()`` and survive a naive log of the settings object or a traceback that
prints locals.
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every environment-driven knob this service has, validated at process boot."""

    model_config = SettingsConfigDict(
        env_prefix="TAXCALC_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    #: DSN the :class:`~langgraph.checkpoint.postgres.PostgresSaver` connects with. No default
    #: that points at localhost on purpose: a plausible-and-wrong default turns a misconfigured
    #: deployment into a service that checkpoints somewhere nobody looks, which is discovered
    #: much later than one that refuses to start.
    postgres_url: str = Field(default="postgresql://postgres:postgres@localhost:5432/postgres")

    #: SSE endpoint of the W7 D4 MCP server. The agent's ONLY tool surface - it never calls the
    #: W3 D1 services directly, because the MCP server is where tenancy, idempotency and the
    #: error-code table already live.
    mcp_sse_url: str = Field(default="http://localhost:8080/sse", min_length=1)

    #: LangSmith project every ``@traceable`` span lands in. One project for the root
    #: ``chat_request`` span and its three named sub-runs, because a request and the nodes it ran
    #: are one causal chain and splitting them means reading two timelines to answer one question.
    langsmith_project: str = Field(default="taxcalc-agent-svc-dev", min_length=1, max_length=128)

    #: Per-request ceiling on Claude spend, in 1e-5 USD minor units. 25000 = $0.25.
    #:
    #: An ``int``, never a float: see :mod:`taxcalc_agent_svc.budgets`. This is the SLOW budget -
    #: it catches a run that is progressing but expensive. :attr:`recursion_limit` is the FAST
    #: one, which catches a run that is looping. Neither subsumes the other.
    cost_ceiling_usd_e5: int = Field(default=25000, ge=1, le=10_000_000)

    #: Super-step ceiling pinned on every ``compile()``. 25, not LangGraph's default 25-by-
    #: coincidence: it is set EXPLICITLY at every call site so that a future feedback edge
    #: (synthesis routing back to retrieval on low confidence) cannot burn thousands of tokens
    #: before a human notices. See :func:`taxcalc_agent_svc.graph.build_taxcalc_agent_graph`.
    recursion_limit: int = Field(default=25, ge=1, le=500)

    #: Model every node calls. One knob, so a model change is one deploy rather than four edits.
    model: str = Field(default="claude-sonnet-4-5", min_length=1)

    #: Per-node deadlines, in seconds, taken from the lesson's p99 measurements. Retrieval is the
    #: cheapest (a local encoder plus two indexed queries), the api node runs a bounded tool-use
    #: loop against a network service, and synthesis pays a generation call - so the budgets are
    #: 3 / 5 / 8 rather than one number applied three times.
    deadline_retrieval_s: float = Field(default=3.0, gt=0.0, le=120.0)
    deadline_api_s: float = Field(default=5.0, gt=0.0, le=120.0)
    deadline_synthesis_s: float = Field(default=8.0, gt=0.0, le=120.0)

    #: Fraction of production traces scored by the RAGAS sampler. 0.01 = 1%. Scoring every trace
    #: would make every production answer pay a second LLM call's latency and cost to grade the
    #: first.
    ragas_sample_rate: float = Field(default=0.01, ge=0.0, le=1.0)

    #: Which reranker the retrieval node asks the sidecar for. Named here rather than read from a
    #: bare env var inside the node so the value is visible in one place and validated at boot.
    reranker: str = Field(default="bge-reranker-base", min_length=1)

    #: Anthropic credential. Secret - never logged. Empty by default so that importing this
    #: module, or constructing Settings in a schema test, needs no credential.
    anthropic_api_key: SecretStr = Field(default=SecretStr(""))
    #: Bearer JWT forwarded to the MCP server, which forwards it to the W3 D1 services.
    bearer_jwt: SecretStr = Field(default=SecretStr(""))

    #: Interface uvicorn binds. ``0.0.0.0`` because the container's port is published by a
    #: Kubernetes ClusterIP service; loopback would make it unreachable from the pod network.
    host: str = Field(default="0.0.0.0")  # noqa: S104 - see the comment above
    port: int = Field(default=8080, ge=1, le=65535)
