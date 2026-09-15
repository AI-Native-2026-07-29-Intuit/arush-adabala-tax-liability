# taxcalc-ai/tests/test_great_expectations_suite.py
"""Great Expectations checkpoint over the real ``doc_chunks`` table, via Testcontainers.

The suite runs against Postgres rather than a Pandas frame, and that is the whole point. A
validation against the DataFrame the loader was handed proves the loader received good data; it
says nothing about what arrived. The failures worth catching all live on the far side of the
insert - a ``vector`` column that took malformed bytes because ``register_vector`` was skipped,
a ``NOT NULL`` that a migration relaxed, an ``ON CONFLICT`` that quietly halved the row count -
and every one of them is invisible to a suite that validates before the write.

The suite is named ``doc_chunks_v1``. The version is in the name because expectations are a
contract with the data and contracts get revised; a later suite that relaxes a bound should be
``doc_chunks_v2`` sitting beside this one, not an edit that leaves no trace of what the floor
used to be.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

import great_expectations as gx

# Imported for its import side effect, and the side effect is load-bearing.
#
# GX computes a `table.column_types` metric before it can evaluate ANY column-level
# expectation, and it builds that metric by reflecting the table through SQLAlchemy and
# compiling each column's type to a string. SQLAlchemy core has never heard of pgvector's
# `vector`, so it reflects `embedding` as NullType(), and compiling a NullType raises
# `CompileError: Can't generate DDL for NullType()`. GX catches that per-expectation and
# reports `"success": false` with an EMPTY result dict - so the symptom is four column
# expectations failing on data that is perfectly valid, with nothing in the report naming a
# type problem.
#
# Importing pgvector.sqlalchemy registers VECTOR in the postgres dialect's `ischema_names`,
# which makes reflection produce a real type and the metric resolve. Without this line the
# suite is red no matter what the data looks like.
import pgvector.sqlalchemy  # noqa: F401
import psycopg
import pytest
from great_expectations.core import ExpectationSuite

# Imported from their defining modules rather than from the `great_expectations.expectations`
# package. That package re-exports them without an explicit `__all__`, and --strict implies
# --no-implicit-reexport, so `gx.expectations.ExpectColumnValuesToNotBeNull` is an attr-defined
# error under the type gate even though it works at runtime.
from great_expectations.expectations.core.expect_column_value_lengths_to_be_between import (
    ExpectColumnValueLengthsToBeBetween,
)
from great_expectations.expectations.core.expect_column_values_to_not_be_null import (
    ExpectColumnValuesToNotBeNull,
)
from great_expectations.expectations.core.expect_table_row_count_to_be_between import (
    ExpectTableRowCountToBeBetween,
)
from sentence_transformers import SentenceTransformer

from taxcalc_ai.corpus import (
    MAX_CHUNK_CHARS,
    MIN_CHUNK_CHARS,
    MODEL_NAME,
    embed_dataframe,
    load_corpus,
)
from taxcalc_ai.pgvector_loader import load_rows

SUITE_NAME: Final[str] = "doc_chunks_v1"
FIXTURE: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "corpus_seed.jsonl"

#: The floor the corpus must clear. 100 is the committed fixture's row count; the ceiling is a
#: sanity bound rather than a capacity limit - a table that has somehow grown past ten million
#: rows means an ingest loop is running away, which is worth failing a build over.
MIN_EXPECTED_ROWS: Final[int] = 100
MAX_EXPECTED_ROWS: Final[int] = 10_000_000


@pytest.fixture(scope="module")
def seeded_dsn(pg_dsn: str) -> str:
    """Seed the shared container with the committed corpus and return its DSN.

    Module-scoped over the session-scoped container: the seed is this module's precondition, and
    running it once here is cheaper than re-embedding 100 chunks per test. ``load_rows`` is
    idempotent, so a re-run is a no-op rather than a duplicate.
    """
    frame = load_corpus(FIXTURE)
    rows = embed_dataframe(frame, model=SentenceTransformer(MODEL_NAME))
    load_rows(pg_dsn, rows)
    return pg_dsn


def _build_suite() -> ExpectationSuite:
    """The ``doc_chunks_v1`` expectations: five checks over the columns that matter.

    Each one corresponds to a way the corpus has a realistic chance of going wrong:

    * ``doc_id`` null - a loader that lost its identifier column mid-transform.
    * ``embedding`` null - the ``NOT NULL`` relaxed, or a row inserted by a path that skipped
      the embedding pass entirely.
    * ``model_version`` null - the column that keeps two models' vectors from being ranked
      against each other; a null here silently opts a row out of that protection.
    * row count - an ``ON CONFLICT`` that over-matched and halved the corpus, or a runaway
      ingest loop.
    * ``chunk_text`` length - the same 1..8000 bound the loader filters on, re-asserted on the
      far side of the write so a bypass of :func:`~taxcalc_ai.corpus.load_corpus` is caught.
    """
    suite = ExpectationSuite(name=SUITE_NAME)
    for expectation in (
        ExpectColumnValuesToNotBeNull(column="doc_id"),
        ExpectColumnValuesToNotBeNull(column="embedding"),
        ExpectColumnValuesToNotBeNull(column="model_version"),
        ExpectTableRowCountToBeBetween(min_value=MIN_EXPECTED_ROWS, max_value=MAX_EXPECTED_ROWS),
        ExpectColumnValueLengthsToBeBetween(
            column="chunk_text", min_value=MIN_CHUNK_CHARS, max_value=MAX_CHUNK_CHARS
        ),
    ):
        suite.add_expectation(expectation)
    return suite


@contextmanager
def _validation(
    dsn: str, build_suite: Callable[[], ExpectationSuite], slug: str
) -> Iterator[tuple[bool, ExpectationSuite]]:
    """Run ``suite`` against ``doc_chunks`` and yield whether it succeeded.

    The SQLAlchemy engine GX builds behind a Postgres data source is pooled, and nothing in the
    ephemeral-context lifecycle disposes it. Its connections are then closed by the garbage
    collector, which surfaces as ``ResourceWarning: unclosed connection`` from
    ``BaseConnection.__del__`` at interpreter shutdown - and this project turns warnings into
    errors, so a leak here fails the run long after the assertion that mattered has passed.
    Disposing it in a ``finally`` keeps that policy intact instead of carving out an exemption
    for a leak this test can simply not have.
    """
    # The context has to exist BEFORE the suite is built: ExpectationSuite.add_expectation
    # reaches for the active data context and raises DataContextRequiredError without one, so
    # the suite arrives here as a factory rather than as an already-populated object.
    context = gx.get_context(mode="ephemeral")
    registered = context.suites.add(build_suite())
    data_source = context.data_sources.add_postgres(
        name=f"taxcalc_corpus_{slug}",
        # SQLAlchemy needs a driver in the URL and psycopg3 is what this project installs, so
        # the DSN is rewritten to the psycopg (v3) dialect rather than the psycopg2 default.
        connection_string=dsn.replace("postgresql://", "postgresql+psycopg://"),
    )
    try:
        asset = data_source.add_table_asset(name=f"doc_chunks_{slug}", table_name="doc_chunks")
        batch_definition = asset.add_batch_definition_whole_table(name="whole_table")
        validation_definition = context.validation_definitions.add(
            gx.ValidationDefinition(name=f"{slug}_run", data=batch_definition, suite=registered)
        )
        result = validation_definition.run()
        yield bool(result.success), registered
    finally:
        data_source.get_engine().dispose()


def test_doc_chunks_suite_passes(seeded_dsn: str) -> None:
    """The ``doc_chunks_v1`` suite validates green against the seeded corpus.

    The expectation count is asserted alongside ``success`` - a suite that lost its expectations
    validates successfully against anything, which is the one way this test could pass while
    checking nothing.
    """
    with psycopg.connect(seeded_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM doc_chunks")
        row = cur.fetchone()
    assert row is not None
    assert int(row[0]) >= MIN_EXPECTED_ROWS, f"seeded only {row[0]} rows"

    with _validation(seeded_dsn, _build_suite, "v1") as (success, suite):
        assert len(suite.expectations) >= 5, "an empty suite passes against anything"
        assert success is True


def test_the_suite_fails_when_the_data_violates_it(seeded_dsn: str) -> None:
    """A deliberately impossible expectation fails, proving the checkpoint can go red.

    Without this, the test above is indistinguishable from a checkpoint that reports success no
    matter what it is pointed at - which is exactly what a misconfigured data source or an empty
    batch produces.
    """

    def build_impossible() -> ExpectationSuite:
        """A suite the seeded corpus cannot satisfy: at least 100 rows, asserted under 10."""
        suite = ExpectationSuite(name="doc_chunks_v1_negative_control")
        suite.add_expectation(ExpectTableRowCountToBeBetween(min_value=0, max_value=9))
        return suite

    with _validation(seeded_dsn, build_impossible, "negative") as (success, _suite):
        assert success is False, "the negative control passed; the checkpoint proves nothing"
