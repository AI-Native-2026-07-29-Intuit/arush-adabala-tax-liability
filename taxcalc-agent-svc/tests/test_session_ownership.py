# taxcalc-agent-svc/tests/test_session_ownership.py
"""Who opens the MCP transport, and who is allowed to close it.

The session is opened LAZILY, on the first request that actually needs a tool - which means it
is opened from inside a LangGraph **node task**, and that task finishes the moment the node
returns. anyio's cancel scopes, which `sse_client` is built on, refuse to be unwound from a task
other than the one that entered them:

    RuntimeError: Attempted to exit cancel scope in a different task than it was entered in

So a transport entered on a shared `AsyncExitStack` and closed at lifespan shutdown raised on
every pod that had served even one tool call. Found by running the eval suite against a live MCP
server: all twenty scenarios succeeded and teardown blew up.

These tests use a stand-in transport that enforces the same rule anyio does, so they fail for the
same reason the real one did, without needing a server.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from taxcalc_agent_svc.runtime import Dependencies
from taxcalc_agent_svc.settings import Settings


class TaskBoundTransport:
    """A context manager that may only be exited by the task that entered it.

    This is anyio's rule, reproduced: `sse_client` wraps a task group, and a task group's
    `__aexit__` checks the running task against the one that entered its cancel scope.
    """

    def __init__(self) -> None:
        self.enter_task: asyncio.Task[Any] | None = None
        self.exit_task: asyncio.Task[Any] | None = None

    async def __aenter__(self) -> tuple[object, object]:
        """:returns: A (read, write) pair, as `sse_client` yields."""
        self.enter_task = asyncio.current_task()
        return object(), object()

    async def __aexit__(self, *exc: object) -> None:
        """:raises RuntimeError: when unwound from a different task than it was entered in."""
        self.exit_task = asyncio.current_task()
        if self.exit_task is not self.enter_task:
            raise RuntimeError(
                "Attempted to exit cancel scope in a different task than it was entered in"
            )


class StubClientSession:
    """Minimal stand-in for `mcp.ClientSession`."""

    def __init__(self, read: object, write: object) -> None:
        self._read = read
        self._write = write

    async def __aenter__(self) -> StubClientSession:
        """:returns: Itself."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """:returns: Nothing."""

    async def initialize(self) -> None:
        """:returns: Nothing."""


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build Settings from a known environment, not from whatever ran before.

    Another module in this suite exercises the reranker selection by setting `RERANKER`, and a
    bare `Settings()` here then fails validation for a credential these tests have no opinion
    about. Tests that pass alone and fail in the suite are worse than tests that fail: the next
    person reads it as flakiness in the code under test.
    """
    for name in ("RERANKER", "TAXCALC_AGENT_RERANKER", "TAXCALC_AI_RERANKER"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def deps(monkeypatch: pytest.MonkeyPatch) -> tuple[Dependencies, TaskBoundTransport]:
    """A Dependencies wired to the task-bound stand-in transport.

    :returns: The holder and the transport, so a test can assert on which task touched it.
    """
    transport = TaskBoundTransport()
    monkeypatch.setattr(
        "taxcalc_agent_svc.runtime.sse_client", lambda *_a, **_kw: transport
    )
    monkeypatch.setattr("taxcalc_agent_svc.runtime.ClientSession", StubClientSession)
    return Dependencies(Settings()), transport


@pytest.mark.asyncio
async def test_a_session_opened_in_a_node_task_closes_cleanly_from_the_lifespan(
    deps: tuple[Dependencies, TaskBoundTransport],
) -> None:
    """The regression: open from a short-lived task, close from another, no RuntimeError.

    `asyncio.create_task` stands in for the LangGraph node task - it is a different task and it
    has *finished* by the time aclose runs, which is exactly the production shape.
    """
    holder, transport = deps

    async def node_task() -> None:
        await holder.session()

    await asyncio.create_task(node_task())
    assert holder.session_ready

    await holder.aclose()  # this raised RuntimeError before the transport got its own task
    assert transport.enter_task is transport.exit_task
    assert transport.enter_task is not asyncio.current_task()


@pytest.mark.asyncio
async def test_the_transport_outlives_the_task_that_asked_for_it(
    deps: tuple[Dependencies, TaskBoundTransport],
) -> None:
    """Opening it in its own task must not mean closing it when the requester finishes.

    A transport unwound as soon as the first node returned would leave every subsequent tool
    call reconnecting - correct-looking in a single-request test and a connection storm in
    production.
    """
    holder, transport = deps

    async def node_task() -> None:
        await holder.session()

    await asyncio.create_task(node_task())
    await asyncio.sleep(0)  # let anything scheduled on the requester's exit run

    assert transport.exit_task is None, "the transport was closed when its requester finished"
    assert holder.session_ready
    await holder.aclose()


@pytest.mark.asyncio
async def test_a_failed_open_releases_the_waiter_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead dependency must raise, not block forever.

    The serving task publishes its failure and sets the ready event in a `finally`; without that
    unconditional set, `session()` awaits an event nobody will ever set and the request hangs
    until its deadline rather than failing with a named cause.
    """
    from taxcalc_agent_svc.runtime import DependencyUnavailable

    @contextlib.asynccontextmanager
    async def refusing(*_a: object, **_kw: object) -> Any:
        """:raises OSError: always, as an unreachable server does."""
        raise OSError("connection refused")
        yield  # pragma: no cover - unreachable, required to make this a generator

    monkeypatch.setattr("taxcalc_agent_svc.runtime.sse_client", refusing)
    monkeypatch.setattr("taxcalc_agent_svc.runtime.OPEN_ATTEMPTS", 1)
    holder = Dependencies(Settings())

    with pytest.raises(DependencyUnavailable, match="MCP server unavailable"):
        await asyncio.wait_for(holder.session(), timeout=5)
    await holder.aclose()
