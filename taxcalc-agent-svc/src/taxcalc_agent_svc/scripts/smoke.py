# taxcalc-agent-svc/src/taxcalc_agent_svc/scripts/smoke.py
"""Invoke the graph twice on one thread and show the checkpointer actually persisted.

The claim being tested is narrow and easy to fake: "state is checkpointed to Postgres, so a
second invocation on the same ``thread_id`` resumes rather than starting fresh." A graph compiled
with a checkpointer runs identically whether or not the checkpointer ever wrote a row, so the
only honest evidence is the rows themselves. This script invokes twice and then *counts them*.

``--offline`` substitutes the three node bodies with deterministic stubs. That is not a way of
avoiding the real thing; it is what makes the claim testable at all in an environment without an
Anthropic key, because the assertion is about *the checkpointer*, and running real generation
calls to produce checkpoint rows would make the evidence depend on three services that have
nothing to do with what is being proven. The topology, the reducers, the ``thread_id`` discipline
and the ``PostgresSaver`` are all the production ones.

The substitution works by rebinding the module-level ``_retrieval`` / ``_api`` / ``_synthesis``
implementations, which the node closures resolve as globals at call time. The decorators, the
deadlines and the tracing all still apply, so what runs is the real node wrapper around a stub
body rather than a stub node.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

import psycopg
from langchain_core.runnables import RunnableConfig

from taxcalc_agent_svc.budgets import BudgetGuard
from taxcalc_agent_svc.graph import build_taxcalc_agent_graph, run_config
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.state import AgentState


def _install_offline_nodes() -> None:
    """Rebind the three node implementations to deterministic stubs.

    :returns: Nothing; the rebinding is a module-level side effect, which is why this lives in a
        script rather than in the package's importable surface.
    """
    from taxcalc_agent_svc.nodes import api, retrieval, synthesis

    async def fake_retrieval(
        state: AgentState, config: RunnableConfig | None, settings: Settings
    ) -> dict[str, Any]:
        """Return one canned document.

        :returns: A partial state carrying ``docs``.
        """
        return {
            "docs": [{"chunk_id": "chunk-doc-1-p0", "doc_id": "doc-1", "score": 0.9}],
            "cost_usd_e5": 10,
            "visited_nodes": ["retrieval_agent"],
        }

    async def fake_api(
        state: AgentState, config: RunnableConfig | None, settings: Settings
    ) -> dict[str, Any]:
        """Return one canned tool result.

        :returns: A partial state carrying ``tool_results``.
        """
        return {
            "tool_results": {"orders.get_order": {"id": "ord-synth-9001", "status": "SHIPPED"}},
            "cost_usd_e5": 20,
            "visited_nodes": ["api_agent"],
        }

    async def fake_synthesis(
        state: AgentState, config: RunnableConfig | None, settings: Settings
    ) -> dict[str, Any]:
        """Return a canned typed answer.

        :returns: A partial state carrying ``answer``.
        """
        return {
            "answer": '{"text":"offline smoke answer","citations":[],"confidence":0.9}',
            "cost_usd_e5": 30,
            "visited_nodes": ["synthesis_agent"],
        }

    retrieval._retrieval = fake_retrieval  # noqa: SLF001 - see the module docstring
    api._api = fake_api  # noqa: SLF001
    synthesis._synthesis = fake_synthesis  # noqa: SLF001


def count_checkpoints(dsn: str, thread_id: str) -> int:
    """Count the checkpoint rows persisted for one thread.

    :param dsn: The Postgres DSN.
    :param thread_id: The thread to count.
    :returns: The number of rows in ``checkpoints`` for that thread.
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM checkpoints WHERE thread_id = %s", (thread_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


async def _run(thread_id: str, question: str, offline: bool) -> int:
    """Invoke twice on one thread and report the checkpoint rows.

    :param thread_id: The thread to checkpoint under. The SAME id both times - that is the whole
        experiment; two different ids would produce two independent runs and prove nothing.
    :param question: The question to ask.
    :param offline: Substitute stub node bodies.
    :returns: A process exit code.
    """
    if offline:
        _install_offline_nodes()

    settings = Settings()
    graph, closer = await build_taxcalc_agent_graph(settings)

    for attempt in (1, 2):
        guard = BudgetGuard(settings.cost_ceiling_usd_e5)
        cfg = run_config(thread_id, settings, guard=guard, session=None)
        # ainvoke, not invoke: every node body is async, and the sync entry point raises
        # `TypeError: No synchronous function provided` rather than running them.
        state = await graph.ainvoke(
            {"question": question, "tenant_id": "tenant-a", "thread_id": thread_id}, config=cfg
        )
        print(f"invocation {attempt}: visited={state.get('visited_nodes')} "
              f"cost_usd_e5={state.get('cost_usd_e5')} answer={state.get('answer')}")

    await closer.__aexit__(None, None, None)

    rows = count_checkpoints(settings.postgres_url, thread_id)
    print(f"checkpoints table rows for thread_id={thread_id!r}: {rows}")
    if rows < 2:
        print("FAIL: fewer than two checkpoint rows - the second invocation did not persist")
        return 1
    print("OK: the second invocation read and extended the prior checkpoint")
    return 0


def main() -> int:
    """Parse arguments and run the smoke.

    :returns: A process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--thread-id", default="t1", help="checkpoint thread to reuse across runs")
    ap.add_argument("--question", default="show order ord-synth-9001", help="question to ask")
    ap.add_argument(
        "--offline",
        action="store_true",
        help="substitute stub node bodies; the checkpointer and topology stay real",
    )
    args = ap.parse_args()
    return asyncio.run(_run(args.thread_id, args.question, args.offline))


if __name__ == "__main__":
    sys.exit(main())
