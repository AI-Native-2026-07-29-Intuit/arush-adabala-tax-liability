# taxcalc-agent-svc/src/taxcalc_agent_svc/app.py
"""FastAPI surface: the lifespan that owns the shared clients, and the one streaming endpoint.

**One MCP session and one checkpointer pool, opened once and shared - but lazily, and not in a
way that can stop the process from listening.** Both are expensive to establish and safe to
share; opening them per request would pay a handshake and a TCP connect on every question asked.
Opening them *eagerly, fatally, in the lifespan* - which is what this module did first - is the
opposite mistake, and a deployment rehearsal is what surfaced it: a process that exits before it
listens is ``CrashLoopBackOff``, so a dependency that was briefly unreachable keeps the service
down long after it returns. :mod:`taxcalc_agent_svc.runtime` holds both behind a lock and a
retry, and the lifespan's warmup is best-effort.

What is emphatically *not* shared is the :class:`~taxcalc_agent_svc.budgets.BudgetGuard`: the
ceiling is per request, and a process-wide guard would let a busy minute exhaust one caller's
budget with another caller's spend.

**Liveness and readiness answer different questions, and are different endpoints.** ``/healthz``
is liveness: the process is up. It reaches no dependency, because a liveness probe that did
would have Kubernetes *restart* a working pod whenever a downstream blipped. ``/readyz`` is
readiness: the graph is compiled, which means the checkpointer is connected, which means a
request can be served. The MCP session is reported there but does **not** gate it - a docs-only
question runs ``retrieval_agent -> synthesis_agent`` and touches no tool, so refusing that
traffic because a different dependency is down would throw away working capacity.

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

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Final

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langsmith.run_helpers import LangSmithExtra
from pydantic import BaseModel, ConfigDict, Field

from taxcalc_agent_svc import __version__
from taxcalc_agent_svc.budgets import BudgetExceeded, BudgetGuard
from taxcalc_agent_svc.graph import run_config
from taxcalc_agent_svc.runtime import Dependencies, DependencyUnavailable
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.sse import TRACE_HEADER, event_stream, new_trace_id

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
        """Construct the dependency holder, warm it best-effort, and close it at shutdown.

        **Nothing here can prevent the process from listening.** ``warmup`` swallows a failure to
        reach either dependency and logs it; the connection is retried on the first request that
        needs it. That is the whole correction described in the module docstring - the previous
        version awaited both connections and let either failure propagate out of the lifespan,
        which under Kubernetes is a pod that never starts rather than one that starts degraded.

        :param application: The app whose state the holder is stashed on.
        :yields: Once, while the service is serving.
        """
        settings = Settings()
        deps = Dependencies(settings)
        application.state.settings = settings
        application.state.deps = deps
        log.info(
            "lifespan.start",
            mcp=settings.mcp_sse_url,
            project=settings.langsmith_project,
            recursion_limit=settings.recursion_limit,
            cost_ceiling_usd_e5=settings.cost_ceiling_usd_e5,
        )
        await deps.warmup()
        # A background retry for the checkpointer, and it is not an optimisation - without it the
        # service deadlocks. /readyz does not open connections by design, so the graph would only
        # ever be opened by an arriving request; but Kubernetes keeps an unready pod out of the
        # Service endpoints, so no request can arrive. Readiness waits on traffic, traffic waits
        # on readiness. Observed in the cluster: Postgres healthy, the pod at 503 forever.
        reconnect = asyncio.create_task(deps.reconnect_forever())
        try:
            yield
        finally:
            reconnect.cancel()
            # Awaited, not just cancelled: cancel() only *requests* cancellation, and a lifespan
            # that returned here would leave the task to be reaped at interpreter shutdown with
            # a "Task was destroyed but it is pending" warning - and, worse, mid-connection.
            with contextlib.suppress(asyncio.CancelledError):
                await reconnect
            await deps.aclose()
            log.info("lifespan.stop")

    application = FastAPI(title="taxcalc-agent-svc", version=__version__, lifespan=lifespan)

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness probe: is this process alive?

        Reports the build so an operator looking at a running pod can tell which one answered
        without shelling into it. Deliberately reaches NO dependency. A liveness probe that
        checked downstreams would have Kubernetes restart a healthy pod whenever one of them
        blipped - converting a partial outage into a total one, and doing it fastest precisely
        when the downstream is already struggling.

        :returns: The service name and version.
        """
        return {"status": "ok", "service": "taxcalc-agent-svc", "version": __version__}

    @application.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        """Readiness probe: can this process serve a request?

        Gated on the graph alone, which means on the checkpointer, because without it no request
        of any shape can run. The MCP session is *reported* but not gated: a docs-only question
        needs no tool, and taking the pod out of service for traffic it could still answer is a
        self-inflicted outage.

        Reads cached state rather than attempting to connect. A probe that opened connections
        would become a source of load every few seconds against a dependency that is, by
        hypothesis, already unwell.

        :param request: The ASGI request, for the app state the lifespan filled.
        :returns: 200 when the graph is up, 503 otherwise, with both dependencies' status.
        """
        deps: Dependencies = request.app.state.deps
        body = {
            "graph": "up" if deps.graph_ready else "down",
            "mcp": "up" if deps.session_ready else "down",
            "version": __version__,
        }
        return JSONResponse(status_code=200 if deps.graph_ready else 503, content=body)

    @application.post("/v1/chat/stream")
    async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
        """Answer one question, streaming in the W4 D4 useChat data-stream format.

        A fresh :class:`~taxcalc_agent_svc.budgets.BudgetGuard` per request - see the module
        docstring on why this one thing is not shared.

        :param req: The validated request body.
        :param request: The ASGI request, for the app state the lifespan filled.
        :returns: The streaming response.
        """
        deps: Dependencies = request.app.state.deps
        settings = deps.settings
        # The graph is required for every request, so it is awaited here and its failure becomes
        # a 503 before any byte of the stream is written - which is the only point at which a
        # status code can still be set.
        graph = await deps.graph()
        # The MCP session is NOT awaited here. A docs-only question never touches a tool, and
        # blocking every request on a dependency a third of them do not need would be the eager
        # lifespan's mistake moved one layer down. The api node resolves it on demand.
        guard = BudgetGuard(settings.cost_ceiling_usd_e5)
        cfg = run_config(req.thread_id, settings, guard=guard, session=deps.session)
        # Minted here, not read from inside the stream. The header must be on the response before
        # the first frame is yielded, and the root run does not exist until the generator is first
        # iterated - which is after the headers have gone out. So the caller chooses the id and
        # tells `@traceable` to use it, rather than asking afterwards for a value that cannot yet
        # exist. Empty when tracing is off; see `new_trace_id`.
        trace_id = new_trace_id()
        # `project_name` overrides the module-level default with the validated setting, so a
        # deployment that points at another project moves the root run - and with it every node
        # span nested underneath - rather than splitting the trace across two projects.
        extra: LangSmithExtra = {"project_name": settings.langsmith_project}
        if trace_id:
            extra["run_id"] = trace_id
        stream = event_stream(
            graph,
            req.question,
            req.tenant_id,
            req.thread_id,
            cfg,
            langsmith_extra=extra,
        )
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={
                # Empty when tracing is off. Set unconditionally so the client can branch on
                # presence rather than on the service's configuration, which it cannot see.
                TRACE_HEADER: trace_id,
                # Without this an intermediary proxy will happily buffer the whole stream and
                # deliver it as one blob at the end, which is a working request that looks
                # exactly like a hung one for its entire duration.
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache",
            },
        )

    @application.exception_handler(DependencyUnavailable)
    async def dependency_handler(
        _request: Request, exc: DependencyUnavailable
    ) -> JSONResponse:
        """Map an unreachable dependency to 503 with ``Retry-After``.

        503 and not 500: the request was well-formed and the service is not broken - something it
        depends on is briefly unreachable, and the same request may well succeed shortly. That
        distinction is what tells an SRE to look at the dependency rather than at this code.

        :param _request: Unused.
        :param exc: The failure.
        :returns: The 503 response.
        """
        return JSONResponse(
            status_code=503,
            content={"error": "dependency_unavailable", "detail": str(exc)},
            headers={"Retry-After": RETRY_AFTER_S},
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
