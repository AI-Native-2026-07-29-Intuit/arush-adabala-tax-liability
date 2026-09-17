# taxcalc-mcp-server/src/taxcalc_mcp_server/app.py
"""FastMCP entry point: the shared lifespan, the logging discipline, and the schema hardening.

Three decisions in this module are the ones every tool inherits.

**One HTTP client, opened once, in the lifespan.** ``@asynccontextmanager`` opens a single
:class:`httpx.AsyncClient` at startup and closes it at shutdown; tools read it off
``mcp.get_context().request_context.lifespan_context``. Constructing a client per call would
open a fresh TCP connection and redo the TLS handshake on every tool invocation - which, in an
agent loop that calls three tools to answer one question, is three handshakes of latency the
user waits through for no benefit. It would also leak sockets, because nothing would close them.

**Logging is pinned to stderr, twice, deliberately.** On the stdio transport *stdout is the
protocol* - it carries newline-delimited JSON-RPC frames and nothing else. A single stray byte
on stdout corrupts the frame the client is mid-parse of and kills the session. So both logging
paths are redirected: :func:`logging.basicConfig` for the standard library (which every
dependency uses, including httpx and the MCP SDK itself) and ``structlog``'s
``PrintLoggerFactory(file=sys.stderr)`` for this server's own structured lines. Pinning only one
of the two leaves the other free to print, which is the subtle version of the same outage.

**The RAG pipeline is imported on first use, on a worker thread.** Importing
:mod:`taxcalc_ai.rag` loads an 80 MB sentence-transformer, reaches out to the model hub to check
for updates, and raises if ``LANGSMITH_API_KEY`` is unset. None of that belongs at module scope,
where every consumer of this module - the schema tests, the description gate, a ``--help`` -
would pay it and one missing credential would stop a server whose other three tools need no
corpus at all.

It does not belong in the lifespan either, which is where it was first put and where it was
measurably wrong: on the SSE transport the lifespan runs as part of serving the first client's
handshake, so that client waited through the whole model load - and, when the hub was
unreachable, through five network retries before the local cache was used. A tool the caller had
not asked for delayed the connection for the ones they had. :func:`rag_entrypoint` therefore
imports on first call, and its only caller invokes it inside ``asyncio.to_thread``, so the load
happens off the event loop and blocks nothing else. Still once per process - module imports are
cached - just paid by the first request that actually wants retrieval.

**Tool errors are raised as protocol errors, not returned as prose.** See
:class:`StructuredErrorFastMCP` - the SDK's default behaviour discards the numeric code that the
entire :mod:`taxcalc_mcp_server.errors` table exists to deliver.

**Tool argument schemas are hardened after registration.** See
:func:`enforce_strict_tool_schemas` - FastMCP's generated top-level argument model ignores
unknown keys, and this server does not want that.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final

import httpx
import psycopg
import redis
import structlog
from mcp import McpError, types
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import INVALID_PARAMS, ContentBlock, ErrorData
from psycopg.rows import TupleRow
from pydantic import ValidationError

from taxcalc_mcp_server import __version__
from taxcalc_mcp_server.settings import Settings

# Logging MUST go to stderr; stdout carries JSON-RPC frames on stdio. See the module docstring.
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)

#: The structured logger every tool emits its ``tool.invoke.start`` / ``tool.invoke.end`` pair on.
log: Final[structlog.stdlib.BoundLogger] = structlog.get_logger("taxcalc-mcp-server")

#: Connect-phase timeout, in seconds. Much tighter than the overall deadline: failing to
#: establish a TCP connection within two seconds means the service is down or unroutable, and
#: spending the rest of the budget waiting does not change that answer.
CONNECT_TIMEOUT_S: Final[float] = 2.0


@dataclass(frozen=True)
class AppCtx:
    """What the lifespan hands every tool handler.

    Frozen because it is shared across concurrent tool invocations: a handler that could rebind
    ``http`` would be rebinding it for every other in-flight request too.

    :ivar http: The one shared client, based at the order service. Tools that call a *different*
        host (``llm.chat``) pass an absolute URL through the same client, reusing its connection
        pool and timeout policy rather than opening a second one.
    :ivar rag_fn: The retrieval pipeline, as ``(question, tenant_id, top_k) -> raw result``. Held
        as a value rather than imported at call time so a test or the latency gate can substitute
        a canned pipeline without monkey-patching a module attribute - and, because
        :func:`rag_entrypoint` hides the pipeline's clients behind this signature, without a
        database either.
    :ivar settings: The validated configuration, read once at boot.
    """

    http: httpx.AsyncClient
    rag_fn: Callable[[str, str, int], dict[str, object]]
    settings: Settings


#: Environment variables the W7 D3 pipeline's clients are built from.
PG_DSN_ENV: Final[str] = "TAXCALC_AI_PG_DSN"
REDIS_URL_ENV: Final[str] = "TAXCALC_AI_REDIS_URL"

#: Lazily-opened pipeline clients, cached for the life of the process. A per-call Postgres
#: connection would spend a TCP handshake and a TLS negotiation on every question asked.
_RAG_CLIENTS: dict[str, object] = {}


def _rag_clients() -> tuple[object, psycopg.Connection[TupleRow], redis.Redis]:
    """Open (once) and return the three clients the W7 D3 pipeline is injected with.

    :returns: The Anthropic client, the corpus connection, and the Redis client.
    :raises KeyError: if :data:`PG_DSN_ENV` or :data:`REDIS_URL_ENV` is unset. Deliberately not
        defaulted to a localhost DSN: a default turns a misconfigured deployment into a process
        that connects somewhere plausible and wrong, which is discovered much later than one
        that refuses.
    """
    if not _RAG_CLIENTS:
        from anthropic import Anthropic

        _RAG_CLIENTS["anthropic"] = Anthropic()
        _RAG_CLIENTS["conn"] = psycopg.connect(os.environ[PG_DSN_ENV])
        _RAG_CLIENTS["redis"] = redis.from_url(os.environ[REDIS_URL_ENV])
        log.info("rag.clients.opened")
    conn = _RAG_CLIENTS["conn"]
    r = _RAG_CLIENTS["redis"]
    assert isinstance(conn, psycopg.Connection)  # noqa: S101 - narrowing a heterogeneous cache
    assert isinstance(r, redis.Redis)  # noqa: S101
    return _RAG_CLIENTS["anthropic"], conn, r


def rag_entrypoint(question: str, tenant_id: str, top_k: int) -> dict[str, object]:
    """Run the W7 D3 pipeline for one question, opening its clients and its model on first use.

    **This function is the injection seam, and its narrow signature is the point.** Everything
    the pipeline needs that is *not* part of the question - the Anthropic client, the corpus
    connection, the Redis handle, the 80 MB encoder - is resolved in here. So a caller that wants
    to substitute a canned pipeline (the fixture-replay gate, a unit test) replaces this one
    callable and needs no database, no Redis and no API key. When those dependencies were reached
    for inside the tool handler instead, a fully stubbed pipeline still demanded a live Postgres,
    which made the latency gate impossible to run anywhere but production.

    Synchronous on purpose: the pipeline is synchronous, and its only caller runs it through
    ``asyncio.to_thread``. That is what makes the deferred import safe here - the model load
    happens on a worker thread rather than on the event loop.

    :param question: The question to answer.
    :param tenant_id: Whose corpus to search.
    :param top_k: Retained for symmetry with the tool's arguments; the pipeline's own stage sizes
        govern retrieval width, and the tool truncates the citation list to ``top_k`` afterwards.
    :returns: The pipeline's raw result - ``text``, ``citations``, ``rerank_timed_out`` and
        ``coverage``.
    """
    from taxcalc_ai.rag import retrieve_and_generate

    anthropic, conn, r = _rag_clients()
    del top_k  # named for the caller's benefit; see the parameter docs
    return retrieve_and_generate(
        question,
        tenant_id,
        anthropic=anthropic,
        conn=conn,
        r=r,
    )


@asynccontextmanager
async def lifespan(_: FastMCP) -> AsyncIterator[AppCtx]:
    """Open the shared HTTP client and the RAG handle at startup; close them at shutdown.

    The ``try``/``finally`` matters: without it a tool that raises during server shutdown would
    skip ``aclose()`` and leak the connection pool, which on the SSE transport means a pod that
    holds sockets open until the kernel reaps them.

    :yields: The :class:`AppCtx` every tool reads its dependencies from.
    """
    s = Settings()
    # The JWT is added per call from the caller's context, not baked in here - one client
    # serving requests from several callers must not carry one caller's credential.
    client = httpx.AsyncClient(
        base_url=s.normalised_orders_url(),
        timeout=httpx.Timeout(s.tool_timeout_default_s, connect=CONNECT_TIMEOUT_S),
    )
    log.info("lifespan.start", orders_svc=s.normalised_orders_url(), project=s.langsmith_project)
    try:
        yield AppCtx(http=client, rag_fn=rag_entrypoint, settings=s)
    finally:
        await client.aclose()
        log.info("lifespan.stop")


class StructuredErrorFastMCP(FastMCP):
    """A :class:`FastMCP` that lets a tool's error *code* reach the client.

    **The defect this fixes.** ``Tool.run`` wraps every exception a handler raises - the one
    exception being the SDK's own ``UrlElicitationRequiredError`` - into a ``ToolError`` whose
    payload is the English string ``"Error executing tool <name>: <message>"``. The MCP layer
    then returns that as a successful result carrying ``isError: true``. So an
    :class:`McpError` raised by :func:`taxcalc_mcp_server.errors._map_http` arrives at the
    client with its ``code`` stripped off, and a caller wanting to know whether it was rate
    limited (4290, back off and retry) or forbidden (4030, stop) has nothing to branch on but
    prose. Everything the centralised error table buys is discarded one layer below it.

    Verified rather than assumed: driving the stdio server as a subprocess and calling
    ``orders.get_order`` for an id the upstream 404s returns ``result.isError`` and the text
    ``'Error executing tool orders.get_order: {"error": "order not found"}'`` - no 4040 anywhere.

    **The fix, and why it takes two pieces.** The error is swallowed at *two* layers, and both
    have to be opened for the code to travel. ``Tool.run`` wraps it into a ``ToolError``, which
    this class unwraps; the low-level ``CallToolRequest`` handler then catches whatever comes out
    and turns it into an ``isError`` result, which :func:`install_structured_error_handler`
    intercepts. Fixing only the first layer strips the ``"Error executing tool ..."`` prefix and
    changes nothing else - the code is still gone - which is exactly what the smoke test showed
    when only half the fix was in place.

    The unwrapping happens here rather than in each handler because the wrapping happens below
    every handler: there is no ``raise`` a tool could write that would survive it.

    **The trade-off, stated plainly.** The MCP specification's ``isError`` convention exists so a
    model can *see* a failure in-band and retry within its own loop. Raising a protocol error
    instead means a strict client surfaces it as an exception. That is the right trade here
    because the consumers are programmatic - the W7 D5 LangGraph router and any other MCP client
    - and their retry logic is written against numeric codes, which the ``isError`` path cannot
    carry. The message text is preserved either way, so nothing is lost but the ambiguity.
    """

    async def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> Sequence[ContentBlock] | dict[str, object]:
        """Dispatch a tool call, re-raising structured errors with their codes intact.

        :param name: The tool to call.
        :param arguments: The call's arguments.
        :returns: Whatever the tool returned.
        :raises McpError: carrying this server's numeric code, for an upstream failure mapped by
            :func:`taxcalc_mcp_server.errors._map_http`, or ``INVALID_PARAMS`` for arguments that
            failed schema validation.
        """
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, McpError):
                structured = cause
            elif isinstance(cause, ValidationError):
                # A rejected argument is a malformed request, and JSON-RPC already has a code for
                # that. Using the standard one rather than inventing a private code means a
                # generic MCP client handles it correctly knowing nothing about this server.
                structured = McpError(ErrorData(code=INVALID_PARAMS, message=str(cause)))
            else:
                raise
            # Recorded as well as raised: the layer above catches exceptions indiscriminately,
            # so the ContextVar is how the error survives that catch. Same task, so it
            # propagates - the low-level handler awaits this call inline.
            _PENDING_ERROR.set(structured)
            # `from None` deliberately: the ToolError wraps a message that already says what went
            # wrong, and chaining it would put that noise in every mapped upstream failure.
            raise structured from None


def install_structured_error_handler(server: FastMCP) -> None:
    """Let a structured tool error out past the low-level handler's blanket ``except``.

    The SDK's ``CallToolRequest`` handler ends in ``except Exception as e: return
    self._make_error_result(str(e))``, which converts any exception - including the
    :class:`McpError` :class:`StructuredErrorFastMCP` just re-raised - into a successful result
    carrying ``isError: true`` and a bare string. Its one exemption is the SDK's own
    ``UrlElicitationRequiredError``, which it re-raises precisely so that ``_handle_request`` can
    turn it into a coded error response. This function extends that same courtesy to this
    server's errors.

    The wrapper reads the error out of a :class:`~contextvars.ContextVar` rather than trying to
    parse it back out of the result, because by then it is a string and the code is gone. Raising
    it here lands in ``_handle_request``'s ``except McpError: response = err.error`` branch,
    which emits a JSON-RPC error carrying the numeric code.

    Idempotent: calling it twice does not stack wrappers, which matters because both transports
    call it and a test may import both.

    :param server: The server whose handler to wrap.
    """
    low = server._mcp_server  # noqa: SLF001 - no public handler registry in mcp 1.x
    original = low.request_handlers.get(types.CallToolRequest)
    if original is None or getattr(original, "_taxcalc_structured", False):
        return

    async def handler(req: types.CallToolRequest) -> types.ServerResult:
        """Run the SDK's handler, then re-raise any structured error it absorbed.

        :param req: The tool-call request.
        :returns: The SDK's result when the call succeeded.
        :raises McpError: carrying this server's numeric code.
        """
        _PENDING_ERROR.set(None)
        result = await original(req)
        pending = _PENDING_ERROR.get()
        if pending is not None:
            raise pending
        return result

    # The marker is set on the wrapper so a second call recognises its own work and does not
    # stack another layer. Underscore-prefixed to keep it out of the way of anything the SDK
    # might put on a handler; the lint exemption is for reading OUR OWN attribute, which is the
    # one case the rule is not about.
    handler._taxcalc_structured = True  # type: ignore[attr-defined]  # noqa: SLF001
    low.request_handlers[types.CallToolRequest] = handler


#: The structured error absorbed by the SDK's blanket ``except`` for the in-flight tool call.
#: A ``ContextVar`` rather than an attribute because one process serves concurrent calls, and an
#: attribute would let one request's error be reported against another's.
_PENDING_ERROR: ContextVar[McpError | None] = ContextVar(
    "taxcalc_mcp_pending_error", default=None
)


#: The server instance every ``@mcp.tool`` decorator in :mod:`taxcalc_mcp_server.tools` registers
#: against. Module-level, so importing a tool module is what registers its tools - which is why
#: both transports import the tool package before calling ``run``.
mcp: Final[StructuredErrorFastMCP] = StructuredErrorFastMCP(
    name="taxcalc-mcp-server", lifespan=lifespan
)

# `version` is not a FastMCP constructor argument in mcp 1.30 - it lives on the low-level server
# underneath, and FastMCP does not forward it. Left unset, the `initialize` handshake reports the
# version of the *mcp package* as this server's version, so a client asking "which build of
# taxcalc-mcp-server am I talking to" is told "1.30.0". That is not a cosmetic difference: the
# committed mcp.json pins "version": "0.1.0", and a client that compares the two would find them
# disagreeing on every deploy. Setting it here keeps one version number for the server.
mcp._mcp_server.version = __version__  # noqa: SLF001 - no public setter in mcp 1.x


def ctx() -> AppCtx:
    """Return the :class:`AppCtx` for the in-flight request.

    A one-line helper repeated in every handler otherwise, and the indirection earns its place:
    the path from a tool to its dependencies runs through four SDK attributes, and spelling it
    out per tool means four chances to get it wrong and no single place to fix it when the SDK
    moves it.

    :returns: The lifespan context.
    """
    app_ctx: AppCtx = mcp.get_context().request_context.lifespan_context
    return app_ctx


def enforce_strict_tool_schemas(server: FastMCP) -> int:
    """Make every registered tool reject unknown arguments, and say so in its published schema.

    **The defect this closes.** FastMCP builds the *top-level* argument model for a tool from
    the handler's signature, using a base whose pydantic config does not set ``extra``. The
    default is therefore ``"ignore"``: a client that sends ``{"order_id": "ord-1", "tenat_id":
    "tenant-a"}`` - a typo, or a hallucinated parameter - has the unknown key silently dropped
    and then fails deep inside the handler with a missing-argument error, or worse, succeeds
    against a default. The published JSON Schema says nothing about it either, so a well-behaved
    client cannot even detect the mistake locally.

    Setting ``extra="forbid"`` on the tool's *input model* (which every one of them does) is not
    enough on its own, because that model is nested one level below the generated one - it
    governs the fields, not the argument envelope. This pass closes the envelope:

    1. ``additionalProperties: false`` on the published schema, so ``tools/list`` advertises the
       strictness and a client validates before spending a round-trip.
    2. ``extra="forbid"`` plus a forced rebuild on the generated model, so the server actually
       *enforces* it rather than advertising a rule it does not apply. Advertising without
       enforcing would be the worse of the two failures: it invites clients to trust a check
       that is not happening.

    It also installs :func:`install_structured_error_handler`, so that the two things a client
    needs in order to trust this server's tool contract - strict arguments and coded errors - are
    turned on by one call that no transport can forget half of.

    Called once per transport entry point, after the tool modules are imported.

    :param server: The server whose registered tools to harden.
    :returns: How many tools were hardened - returned so a caller can assert the tool modules
        were actually imported first. Zero means the decorators never ran, which on a transport
        entry point is a silent "server starts and publishes nothing" bug.
    """
    install_structured_error_handler(server)
    tools = server._tool_manager.list_tools()  # noqa: SLF001 - no public accessor in mcp 1.x
    for tool in tools:
        tool.parameters["additionalProperties"] = False
        arg_model = tool.fn_metadata.arg_model
        arg_model.model_config["extra"] = "forbid"
        # force=True because the core schema was already built when the model was created;
        # without it the config change is cosmetic and the server keeps ignoring extras.
        arg_model.model_rebuild(force=True)
    return len(tools)
