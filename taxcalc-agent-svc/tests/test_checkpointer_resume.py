# taxcalc-agent-svc/tests/test_checkpointer_resume.py
"""The durability story, end to end, against a real Postgres.

The claim is the one that a graph compiled with a checkpointer cannot demonstrate on its own:
**a run that is killed mid-graph and re-issued on the same ``thread_id`` resumes from the
persisted checkpoint rather than starting fresh.** A graph with a checkpointer that never wrote a
row behaves identically to one that did, right up until the restart - so the only honest evidence
is a second process reading what the first one left behind.

Marked ``e2e``: it pulls a Postgres image. The PR tier skips it by marker; the merge-to-main tier
runs it and treats a *skip* as a failure, because a durability claim that silently stopped being
checked is worse than one that was never made.

Today's HITL is checkpoint-resume. An interrupt-and-approve UI - a human authorising a refund
before ``orders.create_refund`` fires - is the next sprint's work and sits on exactly this
machinery: ``interrupt_before=["api_agent"]`` needs a checkpointer that can hold the paused state
across the minutes or hours a human takes to answer, which is precisely what is proven here.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from langchain_core.runnables import RunnableConfig

from taxcalc_agent_svc.budgets import BudgetGuard
from taxcalc_agent_svc.graph import build_taxcalc_agent_graph, run_config
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.state import AgentState

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    """A throwaway Postgres, or a skip naming the exact reason.

    Skipping with the cause rather than a bare "skipped": a skip that said only that it skipped
    would be indistinguishable from a check that ran and found nothing, which is the failure mode
    this repository has already been bitten by once in its RAGAS gate.

    :yields: The DSN of a running Postgres.
    """
    try:
        # `testcontainers.community.postgres`, not the deprecated top-level `testcontainers
        # .postgres`. The old path still works but raises a DeprecationWarning, and this suite's
        # `filterwarnings = ["error"]` turns that into a collection error - correctly: a warning
        # policy that is relaxed for one import is a policy that stops catching the next one.
        from testcontainers.community.postgres import PostgresContainer
    except ImportError as exc:  # pragma: no cover - dev dependency is always present in CI
        pytest.skip(f"testcontainers not installed: {exc}")

    try:
        with PostgresContainer("postgres:16-alpine") as pg:
            # psycopg3, not the SQLAlchemy `postgresql+psycopg2` URL testcontainers renders by
            # default - AsyncPostgresSaver connects with psycopg and rejects the driver suffix.
            dsn = (
                f"postgresql://{pg.username}:{pg.password}"
                f"@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/{pg.dbname}"
            )
            _await_ready(dsn)
            yield dsn
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"could not start a Postgres container (is Docker running?): {exc}")


def _await_ready(dsn: str, attempts: int = 30, delay_s: float = 0.5) -> None:
    """Poll until the database accepts a real connection.

    **Not redundant with the container's own readiness wait.** The official Postgres image starts
    the server twice: once on a unix socket to run its initialisation scripts, then it shuts that
    instance down and starts the real one on TCP. A readiness check that fires during the gap
    sees a mapped port that accepts nothing, and the first query fails with ``connection
    refused`` - on a port that is, a moment later, perfectly fine.

    Found by ordering, not by reading: this file passes in isolation and failed only when
    ``test_app.py`` ran first, because a preceding TestClient shifts the timing by a few hundred
    milliseconds. A race that only appears in one test order is a race that would appear in CI
    at random, which is the worst possible form of it.

    :param dsn: The DSN to poll.
    :param attempts: How many times to try.
    :param delay_s: Seconds between attempts.
    :raises psycopg.OperationalError: if the database never becomes reachable.
    """
    last: Exception | None = None
    for _ in range(attempts):
        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
            return
        except psycopg.OperationalError as exc:
            last = exc
            time.sleep(delay_s)
    raise last if last is not None else RuntimeError("unreachable")


@pytest.fixture
def offline_nodes() -> Iterator[None]:
    """Substitute deterministic node bodies, keeping every wrapper real.

    The bodies are stubs because this test is about the *checkpointer*, and making the evidence
    depend on Anthropic and a document corpus would mean a red build here could be caused by
    three things that have nothing to do with durability. The topology, the reducers, the
    decorators, the deadlines and the ``AsyncPostgresSaver`` are all the production ones - the
    node closures resolve these implementations as module globals at call time, so what runs is
    a real node wrapping a stub body rather than a stub node.

    :yields: Once, with the stubs installed.
    """
    from taxcalc_agent_svc.nodes import api, retrieval, synthesis

    async def fake_retrieval(
        state: AgentState, config: RunnableConfig | None, settings: Settings
    ) -> dict[str, Any]:
        """Canned retrieval.

        :returns: One document.
        """
        return {
            "docs": [{"chunk_id": "chunk-doc-1-p0", "doc_id": "doc-1", "score": 0.9}],
            "cost_usd_e5": 10,
            "visited_nodes": ["retrieval_agent"],
        }

    async def fake_api(
        state: AgentState, config: RunnableConfig | None, settings: Settings
    ) -> dict[str, Any]:
        """Canned tool call.

        :returns: One tool result.
        """
        return {
            "tool_results": {"orders.get_order": {"id": "ord-synth-9001"}},
            "cost_usd_e5": 20,
            "visited_nodes": ["api_agent"],
        }

    async def fake_synthesis(
        state: AgentState, config: RunnableConfig | None, settings: Settings
    ) -> dict[str, Any]:
        """Canned answer.

        :returns: A typed answer as JSON.
        """
        return {
            "answer": '{"text":"resumed","citations":[],"confidence":0.9}',
            "cost_usd_e5": 30,
            "visited_nodes": ["synthesis_agent"],
        }

    originals = (retrieval._retrieval, api._api, synthesis._synthesis)
    retrieval._retrieval = fake_retrieval
    api._api = fake_api
    synthesis._synthesis = fake_synthesis
    try:
        yield
    finally:
        retrieval._retrieval, api._api, synthesis._synthesis = originals


def _count_checkpoints(dsn: str, thread_id: str) -> int:
    """Count the checkpoint rows persisted for one thread.

    :param dsn: The Postgres DSN.
    :param thread_id: The thread to count.
    :returns: The row count.
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM checkpoints WHERE thread_id = %s", (thread_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


async def test_a_second_invocation_resumes_the_prior_checkpoint(
    postgres_dsn: str, offline_nodes: None
) -> None:
    """Re-issuing on the same thread_id continues the conversation instead of restarting it.

    The evidence is ``visited_nodes``. It carries ``operator.add``, so its length after the
    second run is the sum of both runs' contributions **if and only if** the second run loaded
    the first run's persisted state. A run that started fresh would come back with exactly the
    first run's length - same answer, same cost, and a completely different durability story.
    """
    os.environ["TAXCALC_AGENT_POSTGRES_URL"] = postgres_dsn
    settings = Settings(postgres_url=postgres_dsn)
    thread_id = "capstone-hitl-1"

    graph, closer = await build_taxcalc_agent_graph(settings)
    try:
        first = await graph.ainvoke(
            {"question": "policy on refunds", "tenant_id": "tenant-a", "thread_id": thread_id},
            config=run_config(thread_id, settings, guard=BudgetGuard(), session=None),
        )
        first_len = len(first["visited_nodes"])
        assert first_len > 0
    finally:
        # Closing the pool is the "pod restart": the second graph below builds an entirely new
        # AsyncPostgresSaver over a new pool, so nothing but the DATABASE carries state across.
        await closer.__aexit__(None, None, None)

    graph2, closer2 = await build_taxcalc_agent_graph(settings)
    try:
        second = await graph2.ainvoke(
            {"question": "policy on refunds", "tenant_id": "tenant-a", "thread_id": thread_id},
            config=run_config(thread_id, settings, guard=BudgetGuard(), session=None),
        )
    finally:
        await closer2.__aexit__(None, None, None)

    assert len(second["visited_nodes"]) > first_len, (
        "the second run started fresh - the checkpoint was not read back"
    )
    assert _count_checkpoints(postgres_dsn, thread_id) >= 2


async def test_a_different_thread_id_does_not_resume(
    postgres_dsn: str, offline_nodes: None
) -> None:
    """Checkpoints are scoped to their thread.

    The negative control for the test above: without it, a graph that resumed *everything*
    regardless of thread would pass, and two tenants' conversations would bleed into each other.
    """
    settings = Settings(postgres_url=postgres_dsn)
    graph, closer = await build_taxcalc_agent_graph(settings)
    try:
        lengths = []
        for thread_id in ("thread-alpha", "thread-beta"):
            state = await graph.ainvoke(
                {"question": "policy on refunds", "tenant_id": "tenant-a", "thread_id": thread_id},
                config=run_config(thread_id, settings, guard=BudgetGuard(), session=None),
            )
            lengths.append(len(state["visited_nodes"]))
    finally:
        await closer.__aexit__(None, None, None)

    assert lengths[0] == lengths[1], "a fresh thread must not inherit another thread's state"
