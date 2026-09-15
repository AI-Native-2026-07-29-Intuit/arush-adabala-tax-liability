# taxcalc-ai/tests/conftest.py
"""Shared fixtures.

Every fixture builds its object from explicit values rather than from ambient state: the
settings fixture sets real environment variables through ``monkeypatch`` so the test exercises
the same env-reading path production does, and ``monkeypatch`` unwinds them afterwards so one
test cannot leak configuration into the next.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

# Set BEFORE taxcalc_ai.rag is imported anywhere: that module raises at import time on a missing
# LANGSMITH_API_KEY (see its docstring - a credential discovered on the first retrieval is
# discovered in production). conftest is imported before any test module, which makes this the
# one place the value can be in place in time. `setdefault`, so a developer running against a
# real LangSmith workspace keeps their own key.
os.environ.setdefault("LANGSMITH_API_KEY", "lsv2_test_not_a_real_key")
# Tracing OFF for the suite. With it on, every test that touches retrieve_chunks would upload a
# run to somebody's real project using the fake key above, and fail slowly on auth rather than
# quickly on the assertion under test.
os.environ.setdefault("LANGSMITH_TRACING", "false")

from taxcalc_ai.models import Liability, LiabilityEstimateRequest, Taxpayer
from taxcalc_ai.settings import TaxcalcAiSettings

FIXTURES = Path(__file__).parent / "fixtures"

#: The captured Java-side JSON for GET /api/v1/taxpayers/taxpayer-001 - the response body of that
#: request against a locally-running taxcalc-api, re-emitted through TaxpayerReadModel after its
#: money fields gained @JsonFormat(shape = STRING). Every value came off the wire; only the money
#: encoding changed. See PYTHON.md, "The round-trip fixture", for the capture procedure.
JAVA_TAXPAYER_JSON = FIXTURES / "taxpayer_java.json"

PROXY_BASE_URL = "https://proxy.example.internal"
PROXY_API_KEY = "key_synth_abc123"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> TaxcalcAiSettings:
    """A fully-populated settings object, read from test-only environment variables."""
    monkeypatch.setenv("TAXCALC_AI_PROXY_BASE_URL", PROXY_BASE_URL)
    monkeypatch.setenv("TAXCALC_AI_PROXY_API_KEY", PROXY_API_KEY)
    monkeypatch.setenv("TAXCALC_AI_TENANT_ID", "tenant-a")
    monkeypatch.setenv("TAXCALC_AI_PROXY_MAX_RETRIES", "3")
    # Sub-second so the retry test does not actually sleep out the exponential backoff.
    monkeypatch.setenv("TAXCALC_AI_PROXY_TIMEOUT_SECONDS", "1.0")
    return TaxcalcAiSettings()


@pytest.fixture
def java_taxpayer_json() -> bytes:
    """The captured Java-side JSON bytes, read fresh for each test that wants them."""
    return JAVA_TAXPAYER_JSON.read_bytes()


@pytest.fixture
def taxpayer() -> Taxpayer:
    """A valid taxpayer with one liability, built with snake_case (populate_by_name)."""
    return Taxpayer(
        id="taxpayer-001",
        tenant_id="tenant-shared",
        display_name="Ada Lovelace",
        filing_status="SINGLE",
        home_jurisdiction="CALIFORNIA",
        created_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        liabilities=(
            Liability(
                tax_year=2026,
                bracket_id="bracket-ca-2026-mid",
                taxable_amount=Decimal("120000.00"),
                liability_amount=Decimal("26400.00"),
                computed_at=datetime(2026, 1, 15, 12, 0, 5, tzinfo=UTC),
            ),
        ),
        tags=("w7d1", "synthetic"),
    )


@pytest.fixture
def estimate_request(taxpayer: Taxpayer) -> LiabilityEstimateRequest:
    """A valid request envelope wrapping the ``taxpayer`` fixture."""
    return LiabilityEstimateRequest(correlation_id="corr-w7d1-0001", taxpayer=taxpayer)


# ---- Postgres + pgvector container, shared by every test module that needs one ---------------
#
# Session-scoped and declared here rather than in one test module, because three modules want
# the same database: the loader tests, the retrieval tests, and the Great Expectations suite.
# A fixture defined inside a test module is not visible to the others, so each would spin its
# own container - three image pulls and three initdbs for one database's worth of work.

#: The sidecar's own DDL. Applied by the fixture below; idempotent, so re-application is free.
DDL_PATH = Path(__file__).resolve().parent.parent / "sql" / "V001__doc_chunks.sql"


def _await_ready(dsn: str, attempts: int = 60, delay_seconds: float = 0.5) -> None:
    """Block until the container accepts a real connection, not just a TCP handshake.

    The official Postgres entrypoint starts a temporary server for ``initdb``, shuts it down,
    then starts the real one - so "database system is ready to accept connections" appears in
    the logs twice, and a port that was open a moment ago refuses the next connection. Waiting
    on a successful ``SELECT 1`` is the only check that spans that gap; without it the suite
    fails intermittently, on whichever test happens to run first.
    """
    last: Exception | None = None
    for _ in range(attempts):
        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
            return
        except psycopg.OperationalError as exc:  # not ready yet - the initdb restart window
            last = exc
            time.sleep(delay_seconds)
    raise AssertionError(f"postgres never became ready at {dsn}") from last


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    """Spin a Postgres + pgvector container for the session and apply the sidecar's DDL.

    Yields a psycopg3-compatible DSN. ``get_connection_url()`` returns a SQLAlchemy-style
    ``postgresql+psycopg2://`` URL; psycopg3 does not understand the driver suffix, so it is
    stripped here rather than at each call site.
    """
    # testcontainers.community.postgres, not testcontainers.postgres: the short path is a
    # deprecation shim in testcontainers 4.x, and this project's pytest config turns warnings
    # into errors, so the shim fails at collection rather than at runtime.
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        dsn = pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        _await_ready(dsn)
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL_PATH.read_text())
            conn.commit()
        yield dsn
