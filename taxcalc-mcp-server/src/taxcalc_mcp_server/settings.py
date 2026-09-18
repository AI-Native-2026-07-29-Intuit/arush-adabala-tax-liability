# taxcalc-mcp-server/src/taxcalc_mcp_server/settings.py
"""All env-driven config in one place; prefix ``TAXCALC_MCP_``.

Consolidated for the same reason :mod:`taxcalc_ai.settings` is: a missing or malformed value
fails the process at boot with a ``ValidationError`` naming the field, rather than failing the
first tool call that happens to need it - which, for an MCP server, means failing inside an LLM
client's tool loop where the error is least legible.

Two choices carry most of the weight:

``extra="ignore"``
    Deliberately NOT ``forbid`` here, unlike the tool input models. This process is launched by
    Claude Desktop and by Kubernetes, both of which inject environment variables this server has
    no opinion about; ``forbid`` would turn an unrelated ``TAXCALC_MCP_`` variable set by an
    operator into a server that will not start. Strictness belongs on the *tool arguments*,
    where an unexpected key means the model hallucinated a parameter.

``bearer_jwt: SecretStr``
    Renders as ``**********`` in ``repr()``, ``str()`` and ``model_dump()``, so it survives a
    naive log of the settings object and a traceback that prints locals. Exactly one call site
    reaches through it with ``.get_secret_value()`` -
    :func:`taxcalc_mcp_server.tenancy.auth_headers`, which builds the ``Authorization`` header.
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every environment-driven knob this server has, validated at process boot."""

    model_config = SettingsConfigDict(
        env_prefix="TAXCALC_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    #: Base URL of the W3 D1 order service. Trailing slash stripped by
    #: :meth:`normalised_orders_url` so ``f"/orders/{id}"`` never produces a double slash.
    orders_svc_url: str = Field(default="https://taxcalc-orders.internal", min_length=1)
    #: Base URL of the W3 D1 cost-tracked LLM proxy.
    llm_proxy_url: str = Field(default="https://llm-proxy.internal", min_length=1)

    #: Path on the LLM proxy that ``llm.chat`` posts to.
    #:
    #: **Both proxy shapes are supported, and the path selects which one is spoken.** A path
    #: ending in ``/chat/completions`` gets an OpenAI-shaped body (a real ``messages`` array,
    #: ``max_tokens``, and a ``choices[0].message.content`` reply); anything else gets this
    #: capstone's ``POST /v1/completions`` shape (``{prompt, model, feature}``, replying
    #: ``{resolvedModel, inputTokens, outputTokens, text}``). See
    #: :func:`taxcalc_mcp_server.tools.llm._wire_shape`.
    #:
    #: The default is ``/v1/completions`` because that is the route that exists in THIS repo -
    #: ``llmproxy/LlmProxyController.java``, the only proxy the E2E can reach. Defaulting to the
    #: generic ``/v1/chat/completions`` would ship a server whose one LLM tool 404s out of the
    #: box. Point this at ``/v1/chat/completions`` and the chat-shaped upstream works for real,
    #: with no code change. The MCP-facing schema (``messages``/``max_tokens``) is identical
    #: either way: that is the contract, and the wire format below it is an implementation
    #: detail of the deployment.
    llm_proxy_chat_path: str = Field(default="/v1/completions", min_length=1)

    #: LangSmith project every ``@traceable`` span lands in. Shares a project with the W7 D3 RAG
    #: spans on purpose: a ``rag.retrieve_and_generate`` tool call and the retrieval it triggers
    #: are one causal chain, and splitting them across two projects means reading two timelines
    #: to answer one question.
    langsmith_project: str = Field(default="taxcalc-mcp-server", min_length=1, max_length=128)

    #: Deadline for the two HTTP-forwarding tools. Five seconds is a ceiling, not a target: the
    #: caller is an LLM tool loop with its own patience, and a request still in flight after
    #: five seconds has already cost more than the answer is worth.
    tool_timeout_default_s: float = Field(default=5.0, ge=0.5, le=60.0)
    #: Deadline for ``rag.retrieve_and_generate``. An order of magnitude larger because that
    #: tool runs a five-stage pipeline including a cross-encoder forward pass and a generation
    #: call; holding it to the 5 s HTTP budget would time out the healthy path.
    tool_timeout_rag_s: float = Field(default=30.0, ge=1.0, le=300.0)

    #: Bearer JWT forwarded to the W3 D1 services. In stdio mode it arrives from the Claude
    #: Desktop launcher's environment; in SSE mode the per-request ``Authorization`` header wins
    #: over it (see :mod:`taxcalc_mcp_server.tenancy`). Secret - never logged.
    bearer_jwt: SecretStr = Field(default=SecretStr(""))

    #: JWKS endpoint used to validate the SSE bearer locally. Empty disables local validation,
    #: which is the correct default: the Java services validate the same token authoritatively,
    #: and a second validator configured with the wrong issuer rejects tokens the real one
    #: accepts. Set it to make the SSE edge fail fast instead of proxying a doomed request.
    jwks_url: str = Field(default="")
    #: Audience claim required when :attr:`jwks_url` is set.
    jwt_audience: str = Field(default="taxcalc-api")

    #: Interface the SSE transport binds. ``0.0.0.0`` because the container's port is published
    #: by a Kubernetes ClusterIP service; loopback would make it unreachable from the pod network.
    host: str = Field(default="0.0.0.0")  # noqa: S104 - see the comment above
    port: int = Field(default=8080, ge=1, le=65535)

    def normalised_orders_url(self) -> str:
        """Return :attr:`orders_svc_url` without a trailing slash.

        :returns: The base URL, safe to concatenate with a path that begins ``/``.
        """
        return self.orders_svc_url.rstrip("/")

    def normalised_llm_proxy_url(self) -> str:
        """Return :attr:`llm_proxy_url` without a trailing slash.

        :returns: The base URL, safe to concatenate with :attr:`llm_proxy_chat_path`.
        """
        return self.llm_proxy_url.rstrip("/")
