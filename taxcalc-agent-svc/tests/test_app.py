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

import contextlib
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from taxcalc_agent_svc import __version__
from taxcalc_agent_svc.app import create_app
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

    @contextlib.asynccontextmanager
    async def fake_lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Fill app state with fakes instead of opening real clients.

        :param application: The app.
        :yields: Once, while serving.
        """
        application.state.settings = Settings(postgres_url="postgresql://u:p@localhost:1/x")
        application.state.session = object()
        application.state.graph = ScriptedGraph(
            [
                {"event": "on_chat_model_stream", "data": {"chunk": Chunk("hi")}},
                {
                    "event": "on_chain_end",
                    "name": "synthesis_agent",
                    "data": {"output": {"answer": answer}},
                },
            ]
        )
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
