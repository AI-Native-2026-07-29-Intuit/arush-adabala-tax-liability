# taxcalc-agent-svc/src/taxcalc_agent_svc/runtime.py
"""Lazily-opened shared dependencies, and the readiness split they make possible.

**The defect this module exists to fix was found by deploying, not by testing.** The first
version opened the MCP SSE session and the Postgres checkpointer eagerly in the FastAPI lifespan
and let either failure propagate. Under Kubernetes that is a process that exits before it
listens, which is ``CrashLoopBackOff`` - with exponential backoff, so a service whose dependency
was briefly unreachable stays down long after the dependency returns, and a rollout that happens
to coincide with an MCP blip fails outright.

It also contradicted this service's own stated reasoning. ``/healthz`` is documented as
deliberately *not* reaching the MCP server or the database, because "a health check that depended
on every downstream turns one dependency's blip into a cascading restart of a service that was
working fine" - and then the lifespan did exactly that, one layer up, where no probe
configuration could soften it.

**The two dependencies are not equally required, and conflating them was the actual mistake.**

``AsyncPostgresSaver`` (the graph)
    Required for *every* request: the graph cannot be compiled without a checkpointer, so
    nothing can be served. This gates **readiness**.

The MCP ``ClientSession``
    Required only for requests the supervisor routes to ``api_agent``. A docs-only question runs
    ``retrieval_agent -> synthesis_agent`` and touches no tool at all - a third of the committed
    eval suite, and in production the larger share. Refusing to serve those because a *different*
    dependency is unreachable throws away working capacity. So it does **not** gate readiness; it
    is opened on first use and its failure surfaces per request, in the one code path that needs
    it.

Both are opened at most once, behind an :class:`asyncio.Lock`, because a burst of concurrent
requests arriving before the first connection completes must produce one connection and not one
per request. Both are retried with backoff, so a dependency that is merely slow to start - which,
during a rollout, is the normal case - is waited for rather than failed on.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Final

import structlog
from mcp import ClientSession
from mcp.client.sse import sse_client
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from taxcalc_agent_svc.settings import Settings

log: Final[structlog.stdlib.BoundLogger] = structlog.get_logger("taxcalc-agent-svc.runtime")

#: Attempts made when opening a dependency, per call site. Bounded rather than infinite: a caller
#: waiting on a dependency that is genuinely gone should get an error it can act on, not a
#: request that hangs until the client times out.
OPEN_ATTEMPTS: Final[int] = 3

#: Seconds between background attempts to (re)connect the checkpointer.
#:
#: **This loop exists because the first version of this module deadlocked**, and the deadlock was
#: only visible once the service was deployed against a Postgres that started *after* it.
#: ``/readyz`` deliberately does not open connections - a probe that did would hammer a
#: dependency every few seconds precisely when it is already unwell - so the graph was only ever
#: opened by an arriving request. But no request can arrive: Kubernetes keeps an unready pod out
#: of the Service's endpoints. Readiness waited on traffic, traffic waited on readiness, and the
#: pod sat at 503 indefinitely with Postgres healthy beside it.
#:
#: The fix is a background retry owned by the lifespan, which keeps the probe read-only and
#: cheap while still converging. Ten seconds because that is well inside a typical rollout
#: window and far outside anything that would look like load.
RECONNECT_INTERVAL_S: Final[float] = 10.0


class DependencyUnavailable(Exception):
    """Raised when a lazily-opened dependency could not be established.

    Its own type so the FastAPI layer can map it to 503 - the request was well-formed and may
    succeed later - rather than letting a transport error surface as an opaque 500.
    """


class Dependencies:
    """The process-wide clients, opened on first use and shared thereafter.

    One instance per process, held on ``app.state``. Not a module-level singleton: a test that
    needed a second, independently-configured instance would otherwise have to reach into module
    globals, and two tests running in one process would share connections neither of them made.

    :ivar _settings: Validated configuration.
    :ivar _stack: Unwinds every context manager opened here, in reverse, at shutdown.
    """

    def __init__(self, settings: Settings) -> None:
        """Construct the holder. Opens nothing.

        :param settings: Validated configuration.
        """
        self._settings = settings
        self._stack = contextlib.AsyncExitStack()
        self._graph: Any | None = None
        self._session: ClientSession | None = None
        self._graph_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        # The MCP transport is NOT unwound through `self._stack`, and that separation is the
        # whole point of these three fields - see `_serve_session`.
        self._session_task: asyncio.Task[None] | None = None
        self._session_up: asyncio.Event = asyncio.Event()
        self._session_closing: asyncio.Event = asyncio.Event()
        self._session_error: BaseException | None = None

    @property
    def settings(self) -> Settings:
        """The validated configuration this holder was built with.

        :returns: The settings.
        """
        return self._settings

    @property
    def graph_ready(self) -> bool:
        """Whether the graph has been compiled.

        Read by ``/readyz`` *without* attempting to open anything, so a readiness probe cannot
        itself become a source of connection attempts every few seconds.

        :returns: True once the checkpointer is connected and the graph compiled.
        """
        return self._graph is not None

    @property
    def session_ready(self) -> bool:
        """Whether the MCP session is open.

        Reported by ``/readyz`` for an operator's benefit but deliberately not gating it - see
        the module docstring.

        :returns: True once the MCP session has been established.
        """
        return self._session is not None

    async def graph(self) -> Any:
        """Return the compiled graph, building it on first use.

        :returns: The compiled graph.
        :raises DependencyUnavailable: when the checkpointer could not be reached.
        """
        if self._graph is not None:
            return self._graph
        async with self._graph_lock:
            # Re-checked inside the lock: several requests can pass the check above before any of
            # them acquires it, and without this they would each build a graph and a pool.
            if self._graph is not None:
                return self._graph
            from taxcalc_agent_svc.graph import build_taxcalc_agent_graph

            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(OPEN_ATTEMPTS),
                    wait=wait_exponential(multiplier=0.5, max=4),
                    reraise=True,
                ):
                    with attempt:
                        graph, closer = await build_taxcalc_agent_graph(self._settings)
                        self._stack.push_async_exit(closer)
                        self._graph = graph
            except Exception as exc:
                log.warning("graph.open.failed", error=str(exc))
                raise DependencyUnavailable(f"checkpointer unavailable: {exc}") from exc
            log.info("graph.open.ok")
            return self._graph

    def _auth_headers(self) -> dict[str, Any] | None:
        """Build the Authorization header the MCP server requires.

        ``bearer_jwt`` was a **dead setting** until this method existed: declared in Settings,
        documented as "forwarded to the MCP server, which forwards it to the W3 D1 services",
        and read by nothing. ``sse_client`` was called with a URL and no headers, so the agent
        presented no credential at all - and taxcalc-mcp-server's SSE transport refuses an
        unauthenticated connection with 401 *before* the session is established. Measured against
        that server run locally: ``GET /sse -> 401``, body ``missing bearer token``. Every tool
        call the api node makes was therefore unreachable in any deployment where that server
        enforces auth, and the readiness probe would have reported the MCP dependency down
        forever with a configuration that looked complete.

        :returns: The header mapping, or ``None`` when no token is configured - which leaves the
            unauthenticated local case (a server started with no JWKS and no bearer requirement)
            working exactly as it did, rather than sending an empty bearer it would reject.
        """
        token = self._settings.bearer_jwt.get_secret_value()
        return {"Authorization": f"Bearer {token}"} if token else None

    async def session(self) -> ClientSession:
        """Return the MCP client session, opening it on first use.

        :returns: The session.
        :raises DependencyUnavailable: when the MCP server could not be reached.
        """
        if self._session is not None:
            return self._session
        async with self._session_lock:
            if self._session is not None:
                return self._session
            # Opened INSIDE a long-lived task of its own, and closed from that same task.
            #
            # This used to enter `sse_client` straight onto `self._stack`, from whichever task
            # first needed a tool - which, because the session is opened lazily, is a LangGraph
            # NODE task. That task finishes when the node returns. `aclose()` then tried to
            # unwind the transport from the lifespan's task at shutdown, and anyio refused:
            #
            #     RuntimeError: Attempted to exit cancel scope in a different task than it was
            #     entered in
            #
            # Measured by running the eval suite against a live MCP server: every scenario
            # succeeded and teardown raised. In the service that lands in the lifespan's
            # shutdown path, so a pod that had served even one tool call could not shut down
            # cleanly - and the laziness that causes it is deliberate and worth keeping, so the
            # fix is ownership rather than eagerness.
            self._session_task = asyncio.create_task(self._serve_session())
            await self._session_up.wait()
            if self._session_error is not None:
                exc = self._session_error
                raise DependencyUnavailable(f"MCP server unavailable: {exc}") from exc
            # Narrowing for the type checker: `_serve_session` either published a session
            # before setting `_session_up`, or published an error that the branch above
            # re-raised - so `None` is unreachable here. The attribute's declared type still
            # admits it, and an assert states that fact rather than hiding it behind a cast.
            assert self._session is not None  # noqa: S101
            return self._session

    async def _serve_session(self) -> None:
        """Hold the MCP transport open for the process's lifetime, in one task.

        Enter and exit happen here, in the same task, which is what anyio's cancel scopes
        require - see the comment in :meth:`session`. The task parks on
        ``_session_closing`` rather than returning, because returning would unwind the very
        transport its callers are still holding.

        :returns: Nothing. Failures are published on ``_session_error`` for :meth:`session` to
            raise in the caller's own context, where they belong.
        """
        try:
            async with contextlib.AsyncExitStack() as stack:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(OPEN_ATTEMPTS),
                    wait=wait_exponential(multiplier=0.5, max=4),
                    reraise=True,
                ):
                    with attempt:
                        read, write = await stack.enter_async_context(
                            sse_client(self._settings.mcp_sse_url, headers=self._auth_headers())
                        )
                        session = await stack.enter_async_context(ClientSession(read, write))
                        await session.initialize()
                        self._session = session
                log.info("mcp.open.ok", url=self._settings.mcp_sse_url)
                self._session_up.set()
                await self._session_closing.wait()
        except Exception as exc:
            log.warning("mcp.open.failed", url=self._settings.mcp_sse_url, error=str(exc))
            self._session_error = exc
        finally:
            self._session = None
            # Set unconditionally: a caller waiting on this event must be released whether the
            # transport came up or failed, or `session()` blocks forever on a dead dependency.
            self._session_up.set()

    async def warmup(self) -> None:
        """Best-effort eager connect at startup, so a healthy deploy pays no first-request cost.

        Swallows both failures on purpose: this is an *optimisation*, and the whole point of the
        redesign is that neither dependency being briefly unreachable should stop the process
        from listening. What the failure costs is a log line and a slower first request.

        :returns: Nothing.
        """
        for name, opener in (("graph", self.graph), ("mcp", self.session)):
            try:
                await opener()
            except DependencyUnavailable as exc:
                log.warning("warmup.deferred", dependency=name, error=str(exc))

    async def reconnect_forever(self) -> None:
        """Keep trying to open the checkpointer until it succeeds, then stop.

        Owned by the FastAPI lifespan, which cancels it at shutdown. Runs only until the graph is
        up: once ``/readyz`` can pass, a further failure is a *request's* problem and is reported
        to that caller, rather than being retried silently in the background forever.

        Exits immediately when the graph is already up, which is the normal case - warmup usually
        succeeds and this task is then a no-op.

        :returns: Nothing.
        """
        while self._graph is None:
            try:
                await self.graph()
            except DependencyUnavailable:
                await asyncio.sleep(RECONNECT_INTERVAL_S)
            except asyncio.CancelledError:
                # Shutdown. Re-raised rather than swallowed: swallowing it would leave the task
                # un-cancellable and hang the lifespan's shutdown.
                raise
        log.info("reconnect.settled")

    async def aclose(self) -> None:
        """Unwind everything opened here, in reverse order.

        The MCP transport is closed by *asking its own task to finish* rather than by unwinding
        it from here - see :meth:`_serve_session`. Everything else (the checkpointer pool) was
        entered on ``self._stack`` from this same task and unwinds normally.

        :returns: Nothing.
        """
        if self._session_task is not None:
            self._session_closing.set()
            # Shielded from the caller's own cancellation: a lifespan shutdown that is itself
            # being cancelled must still let the transport close, or the socket leaks and the
            # server is left holding a half-open SSE stream.
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(self._session_task)
            self._session_task = None
        await self._stack.aclose()
