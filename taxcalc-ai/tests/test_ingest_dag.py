# taxcalc-ai/tests/test_ingest_dag.py
"""The Airflow ingest DAG's shape: importability, the five tasks, and the retry policy.

Importability is the bar the deliverable sets, and it is the right one. A DAG does not need a
running scheduler for this check to be meaningful: importing the module is what catches a bad
decorator argument, a dependency that is not installed, a typo'd task edge and a missing
``taxcalc_ai_ingest_dag()`` invocation - which are the failures that actually happen. Each of
those otherwise surfaces as "the DAG is not in the UI", hours later, with no error anywhere.

Deliberately NOT tested here: what the task bodies do. Each body is three lines that delegate
to :mod:`taxcalc_ai.chunker`, :mod:`taxcalc_ai.embedder`, :mod:`taxcalc_ai.pgvector_loader` and
:mod:`taxcalc_ai.cache`, all of which have their own container-backed tests. Executing them
through Airflow's runner would need a metadata database and would re-test those modules through
a much more expensive seam.
"""

from __future__ import annotations

from itertools import pairwise

from taxcalc_ai.dags.rag_svc_ingest import TENANTS, taxcalc_ai_ingest_dag

#: The five tasks, in dependency order.
EXPECTED_TASKS = [
    "load_docs",
    "chunk_docs",
    "embed_chunks",
    "upsert_chunks",
    "bump_cache_epochs",
]


def test_the_dag_is_importable_and_registers_five_chained_tasks() -> None:
    """``taxcalc_ai_ingest_dag()`` yields a DAG with the five tasks wired in a single chain.

    The ordering assertion is the one with teeth: TaskFlow derives edges from the data flow, so
    a task that ignored its predecessor's return value would still run - just unordered, in
    parallel with the write it was supposed to follow. For ``bump_cache_epochs`` that is not a
    cosmetic problem: bumping the cache epoch before the upsert commits leaves the cache emptied
    and the corpus unchanged.
    """
    dag = taxcalc_ai_ingest_dag()

    assert dag.dag_id == "taxcalc_ai_ingest"
    assert sorted(dag.task_dict) == sorted(EXPECTED_TASKS)

    # A single chain: each task's only downstream is the next one, and the last has none.
    for upstream, downstream in pairwise(EXPECTED_TASKS):
        assert dag.task_dict[upstream].downstream_task_ids == {downstream}, upstream
    assert dag.task_dict[EXPECTED_TASKS[-1]].downstream_task_ids == set()
    assert dag.task_dict[EXPECTED_TASKS[0]].upstream_task_ids == set()


def test_the_dag_serialises_its_concurrency_and_retry_policy() -> None:
    """``max_active_runs=1``, ``retries=2``, and a 5-minute retry delay, on every task.

    ``max_active_runs=1`` is not throttling for its own sake: two concurrent runs both pass the
    pre-embed gate before either writes, so both pay to embed the same chunks. Serialising is
    cheaper than making the gate transactional.

    The retries are asserted per task rather than on the DAG, because ``default_args`` is
    applied at task construction - a task that overrode it would still leave the DAG-level value
    looking correct.
    """
    dag = taxcalc_ai_ingest_dag()

    assert dag.max_active_runs == 1
    # catchup off, so enabling the DAG does not queue one serialised run per day since the
    # fixed 2026-04-01 start date.
    assert dag.catchup is False

    for task_id in EXPECTED_TASKS:
        task = dag.task_dict[task_id]
        assert task.retries == 2, task_id
        assert task.retry_delay.total_seconds() == 300, task_id


def test_the_dags_tenant_list_matches_the_per_tenant_indexes_in_v002() -> None:
    """The DAG ingests for exactly the tenants ``sql/V002`` built partial HNSW indexes for.

    These two lists are meant to move together and there is nothing to enforce it but this
    test. A tenant ingested without a matching partial index retrieves *correctly* - it falls
    back to the global V001 HNSW - but with degraded recall, silently, with no plan change and
    no error. That is the class of defect a schema assertion is for.
    """
    from pathlib import Path

    ddl = Path(__file__).resolve().parents[1] / "sql"
    v002 = (ddl / "V002__rag2_metadata_and_partial_indexes.sql").read_text()

    for tenant in TENANTS:
        assert f"WHERE tenant_id = '{tenant}'" in v002, tenant
    # And the converse: no partial index exists for a tenant the DAG does not ingest, which
    # would be an index nothing maintains.
    assert v002.count("USING hnsw") == len(TENANTS)
