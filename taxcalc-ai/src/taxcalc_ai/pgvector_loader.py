# taxcalc-ai/src/taxcalc_ai/pgvector_loader.py
"""Batched pgvector insert with ``ON CONFLICT`` idempotency.

This module writes :class:`~taxcalc_ai.corpus.CorpusRow` values into the ``doc_chunks`` table
defined by ``sql/V001__doc_chunks.sql``. Three decisions carry the weight here, and each one is
the difference between a load that can be retried and one that cannot:

**``register_vector(conn)`` before any vector-typed statement.** psycopg does not know what a
``vector`` is; it is an extension type, not a built-in. Without the adapter registered on the
connection, a NumPy array is passed through psycopg's generic object handling and reaches
Postgres as something the ``vector(384)`` column either rejects or accepts as malformed bytes.
The accepting case is the one that hurts - the insert reports success, the row exists, and every
distance computed against it is meaningless. There is no error to find later, only bad search
results, so this call is the first statement inside every connection in this package.

**``ON CONFLICT (doc_id, chunk_idx, model_version) DO UPDATE``, not a plain ``INSERT``.** A bulk
load is exactly the operation that dies halfway: a dropped connection, an OOM-killed worker, a
CI step that timed out. A plain ``INSERT`` makes the retry fail on the rows that already landed,
so recovery means either truncating the table (losing the work that succeeded) or hand-writing a
"which rows made it" query. ``DO UPDATE`` makes the whole load idempotent - re-running it is a
no-op for unchanged rows and a correction for changed ones - which is what lets the retry be
"run it again" rather than a procedure. The conflict target is the table's ``UNIQUE``
constraint; the two are one contract, and an ``ON CONFLICT`` without a matching arbiter index
fails outright rather than degrading.

**``executemany`` over a list of tuples, not a loop of ``execute`` calls.** psycopg pipelines a
batched ``executemany``, so a 100k-row load is a handful of round trips instead of 100k. The
payload is built positionally from ``CorpusRow`` field order, which is why that order is
documented as load-bearing on the dataclass.

The DSN is read from the environment by :func:`dsn_from_env` and never defaulted, so a
misconfigured process fails naming the missing variable instead of quietly connecting to a
local development database.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import Final

import psycopg
from pgvector.psycopg import register_vector

from .corpus import CorpusRow

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.pgvector_loader")

#: The environment variable carrying the Postgres DSN. Read, never defaulted - see
#: :func:`dsn_from_env`.
DSN_ENV_VAR: Final[str] = "TAXCALC_AI_PG_DSN"

#: The insert. The conflict target is the table's ``UNIQUE (doc_id, chunk_idx, model_version)``
#: constraint verbatim; changing one without the other breaks the load rather than the tests.
#:
#: ``DO UPDATE`` refreshes ``chunk_text`` and ``embedding`` but deliberately leaves ``created_at``
#: alone: the column records when a chunk first entered the corpus, and a retry of a partially
#: failed load is not a new arrival. Bumping it would make "when did we ingest this" answer
#: "the last time anything was retried".
_INSERT_SQL: Final[str] = (
    "INSERT INTO doc_chunks "
    "(doc_id, chunk_idx, chunk_text, embedding, model_version, tenant_id) "
    "VALUES (%s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (doc_id, chunk_idx, model_version) DO UPDATE "
    "SET chunk_text = EXCLUDED.chunk_text, embedding = EXCLUDED.embedding"
)


def load_rows(dsn: str, rows: Iterable[CorpusRow]) -> int:
    """Insert (or update) ``rows`` into ``doc_chunks``; safe to retry after partial failure.

    The whole batch commits as one transaction, so a failure mid-way leaves the table exactly as
    it was rather than partially loaded. Combined with the ``ON CONFLICT`` clause that makes the
    recovery procedure "run it again".

    :param dsn: libpq connection string. Normally from :func:`dsn_from_env`; passed explicitly
        so tests can point at a Testcontainers instance without touching process environment.
    :param rows: The rows to write. Consumed once - an exhausted iterator yields nothing, which
        is why the payload is materialised into a list before the connection is opened rather
        than streamed into ``executemany``.
    :returns: The number of rows sent. This is the payload size, not a count of rows *created*:
        an idempotent re-run returns the same number while creating nothing, which is the
        intended reading - it answers "how much was loaded", not "how much was new".
    """
    # Materialised before connecting: building the payload can raise (a row with a wrong-sized
    # vector, an exhausted iterator), and doing it first means that failure never leaves an
    # open connection or an empty transaction behind.
    payload = [
        (r.doc_id, r.chunk_idx, r.chunk_text, r.embedding, r.model_version, r.tenant_id)
        for r in rows
    ]
    if not payload:
        # An empty load is a no-op, not an error: a corpus whose rows were all filtered out is
        # a legitimate outcome of load_corpus, and opening a connection to send nothing is
        # pure cost.
        _LOG.info("pgvector.load.empty", extra={"event": "pgvector.load.empty", "rows": 0})
        return 0

    with psycopg.connect(dsn) as conn:
        # Must precede every vector-typed statement on this connection; see the module docstring.
        register_vector(conn)
        with conn.cursor() as cur:
            cur.executemany(_INSERT_SQL, payload)
        conn.commit()

    _LOG.info(
        "pgvector.load.committed",
        extra={"event": "pgvector.load.committed", "rows": len(payload)},
    )
    return len(payload)


def dsn_from_env() -> str:
    """Read the Postgres DSN from the environment so secrets never appear in source.

    :returns: The DSN held in :data:`DSN_ENV_VAR`.
    :raises KeyError: if the variable is unset. Deliberately not defaulted to a localhost DSN:
        a default turns a misconfigured deployment into a process that connects somewhere
        plausible and wrong, which is discovered much later than a process that refuses to start.
    """
    return os.environ[DSN_ENV_VAR]
