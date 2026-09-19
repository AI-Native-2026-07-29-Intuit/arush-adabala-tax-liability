# taxcalc-agent-svc/src/taxcalc_agent_svc/app.py
"""FastAPI surface: the lifespan that owns the shared clients, and the one streaming endpoint.

**One MCP session and one checkpointer pool, opened once, in the lifespan.** Both are expensive
to establish - the MCP session is an SSE connection plus a protocol handshake, the checkpointer
is a Postgres connection pool - and both are safe to share across concurrent requests. Opening
them per request would pay a handshake and a TCP connect on every question asked, and would leak
both when a request was cancelled mid-flight. What is emphatically *not* shared is the
:class:`~taxcalc_agent_svc.budgets.BudgetGuard`: the ceiling is per request, and a process-wide
guard would let a busy minute exhaust one caller's budget with another caller's spend.

**Errors are mapped by what the caller can do about them.**

``BudgetExceeded`` -> 503 + ``Retry-After``
    Not 402 and not 500. The request was well-formed and the service is healthy; it simply hit a
    spending limit that a later request may not. 503 with ``Retry-After`` is the status a client's
    existing retry logic already understands, and it tells an SRE "capacity/limit", not "bug".

``GraphRecursionError`` -> 500
    This one *is* a bug. The graph looped, and the same request will loop again.

Both are also emitted into the stream on the ``3:`` channel when they happen *after* the response
has begun - see :mod:`taxcalc_agent_svc.sse`. The status-code mapping below is what covers the
window before the first byte, where a status code can still be set.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Final

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from mcp import ClientSession
from mcp.client.sse import sse_client
from pydantic import BaseModel, ConfigDict, Field

from taxcalc_agent_svc import __version__
from taxcalc_agent_svc.budgets import BudgetExceeded, BudgetGuard
from taxcalc_agent_svc.graph import build_taxcalc_agent_graph, run_config
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.sse import TRACE_HEADER, event_stream

log: Final[structlog.stdlib.BoundLogger] = structlog.get_logger("taxcalc-agent-svc")

#: Seconds a client is told to wait after a budget breach. A round number, and deliberately not
#: zero: the per-request ceiling resets immediately, so the delay exists to stop a client from
#: hammering a service whose operator may be mid-way through raising a limit.
RETRY_AFTER_S: Final[str] = "30"


class ChatRequest(BaseModel):
    """One question, from one tenant, on one conversation thread.

    ``extra="forbid"`` here (unlike :class:`~taxcalc_agent_svc.settings.Settings`, which ignores
    extras) because an unexpected key in a *request body* means the caller sent something wrong
    and wants to know, whereas an unexpected environment variable means Kubernetes injected
    something this service has no opinion about.

    :ivar question: The user's question.
    :ivar tenant_id: The requesting tenant. A security boundary - it is the corpus pre-filter and
        the ``tenant_id`` argument forced onto every MCP tool call.
    :ivar thread_id: The conversation to checkpoint under. Reusing one resumes it; a new one
        starts fresh.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    tenant_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)


def create_app() -> FastAPI:
    """Build the FastAPI application with its lifespan.

    A factory rather than a module-level ``app = FastAPI()`` so that importing this module - for
    a schema test, for ``--help``, for the CI type check - opens no SSE connection and no
    database pool. The same reason the MCP server defers its pipeline import.

    :returns: The application.
    """

    @contextlib.asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Open the MCP session and the checkpointer at startup; close both at shutdown.

        ``AsyncExitStack`` rather than nested ``async with`` blocks: the SSE client and the
        ClientSession are two context managers that must both stay open for the life of the
        process and be unwound in reverse order at shutdown. The stack does that unwinding
        correctly even when one of them raises on the way out - which a hand-rolled ``finally``
        chain reliably does not.

        :param application: The app whose state the clients are stashed on.
        :yields: Once, while the service is serving.
        """
        settings = Settings()
        stack = contextlib.AsyncExitStack()

        read, write = await stack.enter_async_context(sse_client(settings.mcp_sse_url))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        graph, closer = await build_taxcalc_agent_graph(settings)
        stack.push_async_exit(closer)

        application.state.settings = settings
        application.state.session = session
        application.state.graph = graph
        log.info(
            "lifespan.start",
            mcp=settings.mcp_sse_url,
            project=settings.langsmith_project,
            recursion_limit=settings.recursion_limit,
            cost_ceiling_usd_e5=settings.cost_ceiling_usd_e5,
        )
        try:
            yield
        finally:
            await stack.aclose()
            log.info("lifespan.stop")

    application = FastAPI(title="taxcalc-agent-svc", version=__version__, lifespan=lifespan)

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness and readiness probe.

        Reports the build so an operator looking at a running pod can tell which one answered
        without shelling into it. Deliberately does NOT call the MCP server or the database: a
        health check that depends on every downstream turns one dependency's blip into a
        cascading restart of a service that was working fine.

        :returns: The service name and version.
        """
        return {"status": "ok", "service": "taxcalc-agent-svc", "version": __version__}

    @application.post("/v1/chat/stream")
    async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
        """Answer one question, streaming in the W4 D4 useChat data-stream format.

        A fresh :class:`~taxcalc_agent_svc.budgets.BudgetGuard` per request - see the module
        docstring on why this one thing is not shared.

        :param req: The validated request body.
        :param request: The ASGI request, for the app state the lifespan filled.
        :returns: The streaming response.
        """
        settings: Settings = request.app.state.settings
        graph = request.app.state.graph
        guard = BudgetGuard(settings.cost_ceiling_usd_e5)
        cfg = run_config(
            req.thread_id, settings, guard=guard, session=request.app.state.session
        )
        stream = event_stream(graph, req.question, req.tenant_id, req.thread_id, cfg)
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={
                # Empty when tracing is off. Set unconditionally so the client can branch on
                # presence rather than on the service's configuration, which it cannot see.
                TRACE_HEADER: "",
                # Without this an intermediary proxy will happily buffer the whole stream and
                # deliver it as one blob at the end, which is a working request that looks
                # exactly like a hung one for its entire duration.
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache",
            },
        )

    @application.exception_handler(BudgetExceeded)
    async def budget_handler(_request: Request, exc: BudgetExceeded) -> JSONResponse:
        """Map a pre-stream budget breach to 503 with ``Retry-After``.

        :param _request: Unused.
        :param exc: The breach.
        :returns: The 503 response.
        """
        return JSONResponse(
            status_code=503,
            content={"error": "budget_exceeded", "detail": str(exc)},
            headers={"Retry-After": RETRY_AFTER_S},
        )

    return application


def run() -> None:
    """Console-script entry point: serve the app with uvicorn.

    Named in ``[project.scripts]`` so ``taxcalc-agent-svc`` is on ``$PATH`` after an install, and
    so the Dockerfile's ``CMD`` runs the same thing a developer runs.

    :returns: Nothing; blocks until the server stops.
    """
    import uvicorn

    settings = Settings()
    uvicorn.run(create_app(), host=settings.host, port=settings.port)
