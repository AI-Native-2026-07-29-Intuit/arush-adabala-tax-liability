# taxcalc-agent-svc/tests/test_app.py
"""The HTTP surface: /healthz answers, and /v1/chat/stream really streams the useChat channels.

This is the ``curl http://localhost:8080/v1/chat/stream`` check from the brief, executed rather
than asserted about - and executed without Anthropic, Postgres or the MCP server, by replacing
the app's *lifespan* rather than its internals. That substitution is the point: the route
handler, the request model, the per-request BudgetGuard construction, the run config and the
StreamingResponse are all the production ones. Only the three things the lifespan opens are fake.

A test that stood up all three services to assert "the endpoint emits ``0:`` then ``2:``" would
be testing three other systems and reporting the result against this one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from taxcalc_agent_svc import __version__
from taxcalc_agent_svc import runtime as runtime_mod
from taxcalc_agent_svc.app import create_app
from taxcalc_agent_svc.runtime import DependencyUnavailable
from taxcalc_agent_svc.settings import Settings


class ScriptedGraph:
    """A graph whose ``astream_events`` replays a fixed script."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        """Construct the graph.

        :param events: The events to replay.
        """
        self._events = events

    def astream_events(
        self, _inputs: dict[str, Any], _config: dict[str, Any], version: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Replay the script.

        :param version: The event-schema version the bridge requested.
        :returns: The async iterator of events.
        """
        assert version == "v2"

        async def gen() -> AsyncIterator[dict[str, Any]]:
            for ev in self._events:
                yield ev

        return gen()


class StubDeps:
    """A stand-in for :class:`~taxcalc_agent_svc.runtime.Dependencies`.

    Mirrors only the four members the HTTP layer touches, so a test cannot accidentally depend on
    holder internals the routes never reach.
    """

    def __init__(self, graph: Any, *, graph_ready: bool = True, session_ready: bool = True) -> None:
        """Construct the stub.

        :param graph: The graph to hand back.
        :param graph_ready: What ``/readyz`` should see for the checkpointer.
        :param session_ready: What ``/readyz`` should see for the MCP session.
        """
        self._graph = graph
        self.graph_ready = graph_ready
        self.session_ready = session_ready
        self.settings = Settings(postgres_url="postgresql://u:p@localhost:1/x")

    async def graph(self) -> Any:
        """Return the scripted graph.

        :returns: The graph.
        """
        return self._graph

    async def session(self) -> Any:
        """Return a placeholder session.

        :returns: An opaque object; the scripted graph never calls a tool.
        """
        return object()


class Chunk:
    """A streamed model chunk."""

    def __init__(self, content: str) -> None:
        """Construct a chunk.

        :param content: The delta text.
        """
        self.content = content


@pytest.fixture
def client() -> Iterator[TestClient]:
    """An app whose lifespan is replaced with one that opens nothing.

    :yields: A test client against the real routes.
    """
    app = create_app()
    answer = json.dumps({"text": "hi", "citations": [], "confidence": 0.9})

    scripted = ScriptedGraph(
        [
            {"event": "on_chat_model_stream", "data": {"chunk": Chunk("hi")}},
            {
                "event": "on_chain_end",
                "name": "synthesis_agent",
                "data": {"output": {"answer": answer}},
            },
        ]
    )

    @contextlib.asynccontextmanager
    async def fake_lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Fill app state with a stub dependency holder instead of opening real clients.

        :param application: The app.
        :yields: Once, while serving.
        """
        application.state.settings = Settings(postgres_url="postgresql://u:p@localhost:1/x")
        application.state.deps = StubDeps(scripted)
        yield

    app.router.lifespan_context = fake_lifespan
    with TestClient(app) as c:
        yield c


def test_healthz_reports_the_running_build(client: TestClient) -> None:
    """An operator can tell which build answered without shelling into the pod."""
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_chat_stream_emits_text_deltas_then_the_typed_answer(client: TestClient) -> None:
    """The endpoint speaks the useChat data-stream protocol over real HTTP."""
    resp = client.post(
        "/v1/chat/stream",
        json={"question": "what is the refund policy", "tenant_id": "tenant-a", "thread_id": "t1"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert '0:"hi"' in body
    assert '2:{"finalAnswer"' in body


def test_the_stream_is_not_buffered_by_an_intermediary(client: TestClient) -> None:
    """``X-Accel-Buffering: no`` is set.

    Without it a proxy will buffer the whole stream and deliver it as one blob at the end - a
    working request that is indistinguishable from a hung one for its entire duration.
    """
    resp = client.post(
        "/v1/chat/stream",
        json={"question": "q", "tenant_id": "tenant-a", "thread_id": "t1"},
    )
    assert resp.headers["x-accel-buffering"] == "no"


def test_an_unknown_body_field_is_rejected(client: TestClient) -> None:
    """``extra="forbid"`` on the request model.

    An unexpected key in a request body means the caller sent something wrong and wants to know -
    unlike an unexpected environment variable, which Kubernetes injects routinely and which
    Settings therefore ignores.
    """
    resp = client.post(
        "/v1/chat/stream",
        json={"question": "q", "tenant_id": "t", "thread_id": "t1", "tenat_id": "typo"},
    )
    assert resp.status_code == 422


def test_an_empty_question_is_rejected(client: TestClient) -> None:
    """``min_length=1``: an empty question would cost a model call to answer nothing."""
    resp = client.post(
        "/v1/chat/stream", json={"question": "", "tenant_id": "t", "thread_id": "t1"}
    )
    assert resp.status_code == 422


def test_readyz_is_gated_on_the_graph_not_the_mcp_session() -> None:
    """A docs-only question needs no tool, so MCP being down must not take the pod out of service.

    This is the readiness split the deployment rehearsal forced. Gating readiness on every
    dependency would refuse traffic the service can still answer - which is a self-inflicted
    outage, and one that fires hardest exactly when a downstream is already struggling.
    """
    app = create_app()

    @contextlib.asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Graph up, MCP down.

        :param application: The app.
        :yields: Once.
        """
        application.state.deps = StubDeps(None, graph_ready=True, session_ready=False)
        yield

    app.router.lifespan_context = lifespan
    with TestClient(app) as c:
        resp = c.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"graph": "up", "mcp": "down", "version": __version__}


def test_readyz_reports_not_ready_without_a_checkpointer() -> None:
    """Without the graph no request of any shape can run, so the pod is genuinely not ready."""
    app = create_app()

    @contextlib.asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Both down.

        :param application: The app.
        :yields: Once.
        """
        application.state.deps = StubDeps(None, graph_ready=False, session_ready=False)
        yield

    app.router.lifespan_context = lifespan
    with TestClient(app) as c:
        resp = c.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["graph"] == "down"


def test_healthz_never_reaches_a_dependency() -> None:
    """Liveness stays green with everything down.

    A liveness probe that checked downstreams would have Kubernetes RESTART a working pod on a
    downstream blip - turning a partial outage into a total one.
    """
    app = create_app()

    @contextlib.asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Everything down.

        :param application: The app.
        :yields: Once.
        """
        application.state.deps = StubDeps(None, graph_ready=False, session_ready=False)
        yield

    app.router.lifespan_context = lifespan
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200


def test_an_unreachable_checkpointer_is_a_503_not_a_500(client: TestClient) -> None:
    """The request was well-formed and the service is not broken - a dependency is unreachable.

    That distinction is what tells an SRE to look at Postgres rather than at this code, and it is
    why the handler maps DependencyUnavailable rather than letting it surface as a 500.
    """
    app = create_app()

    @contextlib.asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """A holder whose graph cannot be built.

        :param application: The app.
        :yields: Once.
        """

        class Broken(StubDeps):
            """Raises on graph()."""

            async def graph(self) -> Any:
                """Fail the way an unreachable Postgres does.

                :raises DependencyUnavailable: always.
                """
                raise DependencyUnavailable("checkpointer unavailable: connection refused")

        application.state.deps = Broken(None)
        yield

    app.router.lifespan_context = lifespan
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post(
            "/v1/chat/stream",
            json={"question": "q", "tenant_id": "t", "thread_id": "t1"},
        )
    assert resp.status_code == 503
    assert resp.headers["retry-after"]
    assert resp.json()["error"] == "dependency_unavailable"


async def test_the_reconnect_loop_converges_once_the_dependency_returns() -> None:
    """A checkpointer that is down at startup and up a moment later ends with a ready pod.

    **This is the deadlock test.** ``/readyz`` deliberately does not open connections, so without
    a background retry the graph is only ever opened by an arriving request - and Kubernetes keeps
    an unready pod out of the Service's endpoints, so no request can arrive. Readiness waits on
    traffic, traffic waits on readiness. Observed in a real cluster before this loop existed:
    Postgres healthy, the pod answering 503 indefinitely.
    """
    from taxcalc_agent_svc.runtime import Dependencies, DependencyUnavailable

    deps = Dependencies(Settings(postgres_url="postgresql://u:p@localhost:1/x"))
    attempts = 0

    async def flaky() -> Any:
        """Fail once, then succeed - a dependency that started late.

        :returns: A stand-in graph.
        :raises DependencyUnavailable: on the first call.
        """
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DependencyUnavailable("connection refused")
        deps._graph = object()
        return deps._graph

    deps.graph = flaky  # type: ignore[method-assign]
    # The interval is shortened through the module object so the test does not sit through a real
    # ten-second backoff; monkeypatch restores it even if the assertion below raises.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(runtime_mod, "RECONNECT_INTERVAL_S", 0.01)
    try:
        await asyncio.wait_for(deps.reconnect_forever(), timeout=5)
    finally:
        monkeypatch.undo()

    assert attempts >= 2
    assert deps.graph_ready


async def test_the_reconnect_loop_is_a_noop_when_the_graph_is_already_up() -> None:
    """The normal case: warmup succeeded, so the background task exits immediately."""
    from taxcalc_agent_svc.runtime import Dependencies

    deps = Dependencies(Settings(postgres_url="postgresql://u:p@localhost:1/x"))
    deps._graph = object()
    await asyncio.wait_for(deps.reconnect_forever(), timeout=2)
    assert deps.graph_ready


async def test_the_reconnect_loop_is_cancellable() -> None:
    """Shutdown must not hang on a dependency that never returns."""
    from taxcalc_agent_svc.runtime import Dependencies, DependencyUnavailable

    deps = Dependencies(Settings(postgres_url="postgresql://u:p@localhost:1/x"))

    async def never() -> Any:
        """Always fail.

        :raises DependencyUnavailable: always.
        """
        raise DependencyUnavailable("still down")

    deps.graph = never  # type: ignore[method-assign]
    task = asyncio.create_task(deps.reconnect_forever())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
