# taxcalc-ai/src/taxcalc_ai/dags/rag_svc_ingest.py
"""TaskFlow ingest DAG: load -> chunk -> embed -> upsert -> bump the cache epoch.

Five tasks, chained, each one a seam that can fail and be retried independently. The chain is
not an aesthetic choice: it is where the recovery boundaries are.

**The epoch bump is a SEPARATE, FINAL task, and that ordering is the whole point.** Bumping the
per-tenant cache epoch invalidates every cached answer for that tenant, so it must happen after
the new chunks are committed and never before. Folded into ``upsert_chunks`` it would run inside
the same task as the write - and a task that fails after the upsert but before the bump would
retry the upsert (idempotent, harmless) *and* the bump (an extra invalidation, also harmless).
Folded the other way round - bump first - a failed upsert would leave the cache emptied and the
corpus unchanged: every subsequent question pays full price to rebuild answers identical to the
ones just thrown away. Last, and only on success, is the only correct position.

**``max_active_runs=1``.** The ingest writes to ``doc_chunks`` and bumps epochs. Two concurrent
runs would interleave their upserts (safe, thanks to ``ON CONFLICT``) and their epoch bumps
(safe individually, but the second run's bump can land between the first run's upsert and its
own bump, invalidating a cache that was about to be invalidated anyway). The real cost is
duplicated embedding work: two runs both pass the pre-embed gate before either writes, so both
pay for the same vectors. Serialising is cheaper than making the gate transactional.

**``retries=2`` with a 5-minute delay.** The failures this pipeline actually sees are transient
and slow to clear: a Postgres restart, a model download, a Redis failover. Three attempts over
ten minutes covers those; retrying faster would just exhaust the budget inside the same outage.
Every task is idempotent - the pre-embed gate skips unchanged chunks, ``ON CONFLICT DO UPDATE``
absorbs a repeated write, and an extra ``INCR`` only invalidates a cache twice - so a retry is
safe at any point in the chain.

**Only the expensive imports are lazy.** ``langchain_core`` is a pydantic model package and is
imported at module scope so the task signatures can be typed honestly; ``sentence_transformers``,
``psycopg`` and ``redis`` are imported inside the tasks that use them, because the scheduler
parses this file continuously and has no business loading ~80 MB of model weights or opening a
driver it never calls.

**Importability is the bar, not a live scheduler.** The DAG does not need to be running for the
gate to be meaningful; ``python -c "from taxcalc_ai.dags.rag_svc_ingest import
taxcalc_ai_ingest_dag"`` catching an import error, a bad decorator argument or a typo'd task
dependency is what CI checks, and it costs a second instead of a scheduler.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Final

from airflow.sdk import dag, task
from langchain_core.documents import Document

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.dags.rag_svc_ingest")

#: Tenants this DAG ingests for. Enumerated rather than discovered, and deliberately the same
#: three the per-tenant partial HNSW indexes in ``sql/V002`` exist for: a tenant ingested here
#: without a matching index would retrieve correctly but with degraded recall, silently, so the
#: two lists are meant to be edited together.
TENANTS: Final[tuple[str, ...]] = ("tenant-a", "tenant-b", "tenant-c")

#: Environment variable carrying the Redis URL for the cache epoch counters.
REDIS_URL_ENV: Final[str] = "TAXCALC_AI_REDIS_URL"

#: Environment variable naming the corpus file this run ingests. Read at task-execution time
#: rather than at DAG-parse time: the scheduler parses this module every few seconds, and a
#: ``KeyError`` at parse time breaks the whole DAG bag rather than one run.
CORPUS_PATH_ENV: Final[str] = "TAXCALC_AI_CORPUS_PATH"


@dag(
    dag_id="taxcalc_ai_ingest",
    description="Daily ingest for taxcalc-ai (load -> chunk -> embed -> upsert -> bump epoch).",
    # A fixed past date, not `days_ago(1)` and not `datetime.now()`: a moving start_date makes
    # the DAG's run history un-reproducible, and Airflow deprecated the helper for that reason.
    start_date=datetime(2026, 4, 1),
    schedule="@daily",
    # catchup=False, so enabling the DAG does not immediately queue one run per day since
    # start_date. With max_active_runs=1 that backfill would serialise into a long queue of runs
    # all ingesting the same current corpus file.
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "owner": "platform-svc",
    },
    tags=["taxcalc_ai", "rag-svc"],
)
def taxcalc_ai_ingest_dag() -> None:
    """Build the ingest DAG.

    Every task imports the work it does from :mod:`taxcalc_ai` rather than implementing it, so
    the pipeline the DAG orchestrates is the same code the test suite covers. Imports are
    function-local on purpose: the scheduler parses this file continuously, and importing
    ``sentence_transformers`` at module scope would load ~80 MB of weights into the scheduler
    process, which never embeds anything.
    """

    @task
    def load_docs() -> list[str]:
        """Read the corpus file and return the ``doc_id`` values it contains.

        Returns ids rather than the documents themselves because the return value crosses
        XCom, which is a metadata-database row - fine for a list of identifiers, wrong for a
        corpus. The next task re-reads the file it needs.

        :returns: The distinct ``doc_id`` values in the configured corpus file.
        """
        from pathlib import Path

        from taxcalc_ai.corpus import load_corpus

        frame = load_corpus(Path(os.environ[CORPUS_PATH_ENV]))
        doc_ids = sorted({str(value) for value in frame["doc_id"].tolist()})
        _LOG.info(
            "ingest.load.completed",
            extra={"event": "ingest.load.completed", "documents": len(doc_ids)},
        )
        return doc_ids

    @task
    def chunk_docs(doc_ids: list[str]) -> int:
        """Chunk the named documents and return how many chunks they produced.

        A count, not the chunks: see ``load_docs`` on XCom. The chunking is re-done in
        ``embed_chunks`` from the same deterministic input, which is cheap (pure text work) and
        avoids putting the corpus through the metadata database.

        :param doc_ids: Ids from ``load_docs``.
        :returns: The number of chunks the corpus yields.
        """
        from taxcalc_ai.chunker import chunk_docs as split

        chunks = split(_documents_for(doc_ids))
        _LOG.info(
            "ingest.chunk.completed",
            extra={"event": "ingest.chunk.completed", "chunks": len(chunks)},
        )
        return len(chunks)

    @task
    def embed_chunks(chunk_count: int) -> int:
        """Run the pre-embed gate and embed only what it lets through.

        ``chunk_count`` is taken as an argument purely to express the dependency - Airflow's
        TaskFlow API derives the edge from the data flow, so a task that ignored its
        predecessor's output would have to declare the ordering separately and could drift
        from it.

        :param chunk_count: The count from ``chunk_docs``, used to log the gate's saving.
        :returns: The number of chunks the gate left to embed.
        """
        from taxcalc_ai.chunker import chunk_docs as split
        from taxcalc_ai.embedder import candidates_from_chunks, pending_candidates
        from taxcalc_ai.pgvector_loader import dsn_from_env

        candidates = candidates_from_chunks(split(_documents_for(None)))
        pending = pending_candidates(dsn_from_env(), candidates)
        _LOG.info(
            "ingest.embed.gated",
            extra={
                "event": "ingest.embed.gated",
                "chunks": chunk_count,
                "pending": len(pending),
                "skipped": len(candidates) - len(pending),
            },
        )
        return len(pending)

    @task
    def upsert_chunks(pending_count: int) -> int:
        """Embed the pending chunks and upsert them into ``doc_chunks``.

        The embed and the write are one task rather than two because there is nothing useful to
        do with a vector that has not been written: splitting them would put a list of 384-float
        arrays through XCom to gain a retry boundary that saves no work.

        :param pending_count: The count from ``embed_chunks``, expressing the dependency.
        :returns: The number of rows written. Zero is the success case for a re-run.
        """
        from taxcalc_ai.chunker import chunk_docs as split
        from taxcalc_ai.embedder import candidates_from_chunks, embed_pending
        from taxcalc_ai.pgvector_loader import dsn_from_env

        written = embed_pending(dsn_from_env(), candidates_from_chunks(split(_documents_for(None))))
        _LOG.info(
            "ingest.upsert.completed",
            extra={
                "event": "ingest.upsert.completed",
                "pending": pending_count,
                "written": written,
            },
        )
        return written

    @task
    def bump_cache_epochs(upserted: int) -> None:
        """Invalidate every tenant's semantic cache, last and only on success.

        One ``INCR`` per tenant, so a tenant's entire cache becomes unreachable in a single
        atomic write with no keyspace scan. See the module docstring for why this is the final
        task and not part of the upsert.

        :param upserted: The row count from ``upsert_chunks``, expressing the dependency.
        """
        import redis

        from taxcalc_ai.cache import bump_epoch

        client = redis.from_url(os.environ[REDIS_URL_ENV])
        for tenant in TENANTS:
            epoch = bump_epoch(client, tenant)
            _LOG.info(
                "ingest.epoch.bumped",
                extra={
                    "event": "ingest.epoch.bumped",
                    "tenant_id": tenant,
                    "epoch": epoch,
                    "upserted": upserted,
                },
            )

    # The chain. Written as named steps rather than one nested call so the pipeline reads in
    # execution order and a future task can be inserted without re-nesting four calls.
    #
    # The ignores are Airflow's typing, not a defect here: a @task-decorated function keeps its
    # ORIGINAL signature for static purposes, but CALLING it inside a DAG body returns an
    # XComArg - a lazy reference to that task's output, resolved by the scheduler at run time -
    # rather than the declared return value. So every edge in a TaskFlow DAG is an
    # XComArg-where-a-value-is-declared mismatch. The alternative is dropping the return
    # annotations, which would lose the type checking on the task bodies themselves, where the
    # real logic is. Each ignore is narrow (arg-type) and warn_unused_ignores keeps them honest.
    ids = load_docs()
    n_chunks = chunk_docs(ids)  # type: ignore[arg-type]
    n_emb = embed_chunks(n_chunks)  # type: ignore[arg-type]
    n_ups = upsert_chunks(n_emb)  # type: ignore[arg-type]
    bump_cache_epochs(n_ups)  # type: ignore[arg-type]


def _documents_for(doc_ids: list[str] | None) -> list[Document]:
    """Read the configured corpus file into LangChain documents, optionally filtered.

    Module-level rather than nested inside a task so every task shares one definition of "what
    the corpus is" - three of the five tasks need it, and a nested copy in each is three places
    for the corpus format to drift.

    :param doc_ids: Restrict to these documents, or ``None`` for the whole corpus.
    :returns: One document per corpus row, carrying ``doc_id`` and ``tenant_id`` metadata.
    """
    from pathlib import Path

    from langchain_core.documents import Document

    from taxcalc_ai.corpus import load_corpus

    frame = load_corpus(Path(os.environ[CORPUS_PATH_ENV]))
    if doc_ids is not None:
        frame = frame[frame["doc_id"].isin(doc_ids)]
    return [
        Document(
            page_content=str(record["chunk_text"]),
            metadata={
                "doc_id": str(record["doc_id"]),
                "tenant_id": str(record["tenant_id"]),
            },
        )
        for _, record in frame.iterrows()
    ]


# Airflow discovers a DAG by finding a DAG object in the module's globals. The @dag-decorated
# function returns one when CALLED, so this invocation is what registers it - without it the
# module parses cleanly and contributes no DAG at all, which is a failure that looks exactly
# like a correct file.
taxcalc_ai_ingest_dag()
