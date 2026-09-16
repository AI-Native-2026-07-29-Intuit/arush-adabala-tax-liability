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
from typing import Final

import psycopg
import pytest
import redis
from _pytest.terminal import TerminalReporter

# Set BEFORE taxcalc_ai.rag is imported anywhere: that module raises at import time on a missing
# LANGSMITH_API_KEY (see its docstring - a credential discovered on the first retrieval is
# discovered in production). conftest is imported before any test module, which makes this the
# one place the value can be in place in time. `setdefault`, so a developer running against a
# real LangSmith workspace keeps their own key.
#
# The stand-in value deliberately bears NO resemblance to a real LangSmith key: a fixture
# carrying the vendor's own key prefix is a hit for any repo-wide secret sweep, and a scan that
# returns known-harmless matches is a scan reviewers learn to skim. That prefix earns its
# meaning by appearing nowhere in this tree except the pattern of the grep that hunts for it.
#
# Written as a literal rather than as a named constant on purpose: ruff's E402 tolerates
# environment setup ahead of the imports below but not an assignment, and the constant that
# other modules import is therefore defined after them.
os.environ.setdefault("LANGSMITH_API_KEY", "langsmith-test-not-a-real-key")
# Tracing OFF for the suite. With it on, every test that touches retrieve_chunks would upload a
# run to somebody's real project using the fake key above, and fail slowly on auth rather than
# quickly on the assertion under test.
os.environ.setdefault("LANGSMITH_TRACING", "false")
# RAGAS ships usage telemetry that is on by default. Two reasons this is off here, and the
# second is the one that matters: it leaks an unclosed handle on its uuid.json under
# `_analytics.py`, which surfaces as a ResourceWarning at teardown and fails the run under this
# project's filterwarnings=error policy - and, more importantly, a work repository's CI should
# not be making unsolicited outbound calls to a third party on every build. Disabling it fixes
# the leak by removing the code path rather than by exempting its warning.
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")

from taxcalc_ai.models import Liability, LiabilityEstimateRequest, Taxpayer
from taxcalc_ai.settings import TaxcalcAiSettings

#: The LangSmith credential the suite is actually running with - the stand-in set above, or a
#: developer's real key when one was already in the environment. Read back rather than
#: re-declared so that a test which has to remove and restore the variable (see
#: test_rag_traceable.py's import-time check) puts back what was there, instead of replacing a
#: working key with a placeholder for every test that follows it.
SUITE_LANGSMITH_API_KEY = os.environ["LANGSMITH_API_KEY"]

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

#: The sidecar's own DDL, in application order. Applied by the fixture below; every statement is
#: ``IF NOT EXISTS``, so re-application is free.
#:
#: V002 (W7 D3) is applied SEPARATELY and with ``autocommit=True``, because every index in it is
#: ``CREATE INDEX CONCURRENTLY`` and Postgres rejects that inside a transaction block. psycopg3
#: opens an implicit transaction on a connection's first statement, so running both files on one
#: connection would fail on V002's first index with "cannot run inside a transaction block".
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
DDL_PATH = SQL_DIR / "V001__doc_chunks.sql"
DDL_V002_PATH = SQL_DIR / "V002__rag2_metadata_and_partial_indexes.sql"


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


def _split_statements(ddl: str) -> list[str]:
    """Split a DDL file into individual statements, dropping comments and blank lines.

    Naive on purpose - it splits on ``;`` and would mangle a semicolon inside a string literal
    or a dollar-quoted body. Neither appears in this project's DDL, and a real SQL parser for
    two files of ``CREATE INDEX`` would be more machinery than the problem deserves. The
    ``sqlglot``-shaped solution becomes correct to reach for the first time a function body
    lands in ``sql/``.
    """
    stripped = "\n".join(line for line in ddl.splitlines() if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in stripped.split(";") if statement.strip()]


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
        # V002 on its own autocommit connection - see the note on DDL_V002_PATH. Split into
        # individual statements because CREATE INDEX CONCURRENTLY also cannot be sent in a
        # multi-statement batch: psycopg wraps a multi-statement execute() in one implicit
        # transaction, which puts it right back inside the block it must not be in.
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            for statement in _split_statements(DDL_V002_PATH.read_text()):
                cur.execute(statement)
        yield dsn


# ---- Redis container, shared by the cache tests and the pipeline test ------------------------
#
# Session-scoped and declared here for the same reason pg_dsn is: two modules want one Redis,
# and a fixture defined inside a test module is invisible to the other one.


@pytest.fixture(scope="session")
def redis_client() -> Iterator[redis.Redis]:
    """Spin a Redis container for the session and yield a client against it.

    Readiness is established with a real ``PING`` rather than a port check: the container's port
    is bound before the server finishes loading, so a TCP handshake succeeds against a server
    that will refuse the next command.
    """
    # testcontainers.community.redis, not testcontainers.redis: the short path is a deprecation
    # shim in testcontainers 4.x, and this project's filterwarnings=error policy makes the shim
    # fail at collection rather than at runtime.
    from testcontainers.community.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as container:
        client: redis.Redis = redis.Redis(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(6379)),
            db=0,
        )
        for _ in range(60):
            try:
                client.ping()
                break
            except redis.ConnectionError:  # not accepting commands yet
                time.sleep(0.5)
        else:
            raise AssertionError("redis never became ready")
        yield client


# ---- CI visibility: a skipped gate must not read as a gate that passed -----------------------
#
# pytest reports a skip as a non-failure, and GitHub Actions reports a step that exited 0 as a
# green check. Compose those two and a gate that evaluated NOTHING renders identically to one
# that evaluated everything and was satisfied - which is exactly the state the RAGAS threshold
# step is in while the evaluator workspace is spend-capped, and exactly the state a reviewer
# scanning the checks list cannot distinguish from a measured baseline.
#
# The honest fix is not to turn the skip into a failure: the credential is un-buyable until the
# cap lifts, and a permanently-red required step teaches people to ignore it. It is to make the
# skip VISIBLE at the layer the reviewer is actually looking at - a workflow annotation on the
# run and a line in the job summary - so "green" still means "nothing is broken" while the
# summary says, in the reviewer's eyeline, which floors went unmeasured.
#
# Local runs are untouched: `-ra` already prints skip reasons to a developer who is watching the
# output scroll past. This exists for the reader who only ever sees the checkmark.

#: Set to "true" on every GitHub-hosted runner. Absent everywhere else, which is what keeps the
#: annotation syntax out of a developer's terminal.
_GITHUB_ACTIONS_ENV: Final[str] = "GITHUB_ACTIONS"

#: Path to the markdown file whose contents become the job summary panel on the run page.
_STEP_SUMMARY_ENV: Final[str] = "GITHUB_STEP_SUMMARY"

#: Prefix pytest puts on the reason it stores in a skipped report's ``longrepr``.
_SKIP_PREFIX: Final[str] = "Skipped: "

#: Collected across the session and emitted once at the end, rather than written as each skip
#: happens: ``pytest_runtest_logreport`` fires while pytest's output capture is active, so a
#: workflow command written there can be swallowed. ``pytest_terminal_summary`` runs after
#: capture has been torn down, which is the only point the annotation reliably reaches stdout.
_skipped_tests: Final[list[tuple[str, str]]] = []


def _escape_annotation(text: str) -> str:
    """Encode ``text`` for a ``::warning::`` workflow command.

    Actions parses these line by line, so a reason containing a newline would truncate the
    annotation at the break and leave the remainder on stdout as stray text. The three
    substitutions are the ones the workflow-command format defines; ``%`` goes first, because
    doing it after the others would re-encode the ``%`` they just introduced.
    """
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _skip_reason(report: pytest.TestReport) -> str:
    """Pull the human-readable reason out of a skipped report.

    A skip's ``longrepr`` is a ``(path, lineno, "Skipped: <reason>")`` triple rather than a
    traceback object. The prefix is stripped because it is pytest's own framing, and the
    annotation supplies its own.
    """
    longrepr = report.longrepr
    reason = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
    return reason.removeprefix(_SKIP_PREFIX).strip()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Record every skip, wherever in the test's lifecycle it was raised.

    Both phases matter and neither duplicates the other: a ``skipif`` decorator skips during
    setup, while a ``pytest.skip()`` inside the body skips during the call. The RAGAS gate can
    do either - no credential at all takes the first path, a credential whose workspace is
    capped takes the second - and a hook that watched only one phase would miss half the ways
    that gate goes quiet.
    """
    # An xfailed test is also reported as "skipped" but carries `wasxfail`. It is a recorded
    # expectation rather than an unrun check, so it is not what this is warning about.
    if not report.skipped or hasattr(report, "wasxfail"):
        return
    _skipped_tests.append((report.nodeid, _skip_reason(report)))


def pytest_terminal_summary(terminalreporter: TerminalReporter) -> None:
    """On a GitHub runner, re-report the session's skips as annotations and a summary block.

    Written through the terminal reporter rather than ``print``: it is the writer pytest itself
    uses for the summary section, so the lines land on the step's real stdout in order, which is
    what the Actions log parser reads workflow commands from.
    """
    if not _skipped_tests or os.environ.get(_GITHUB_ACTIONS_ENV) != "true":
        return

    for nodeid, reason in _skipped_tests:
        terminalreporter.write_line(
            f"::warning title=Skipped (not evaluated): {nodeid}::{_escape_annotation(reason)}"
        )

    summary_path = os.environ.get(_STEP_SUMMARY_ENV)
    if not summary_path:
        return
    lines = ["", "### :warning: Tests skipped - these checks did not run", ""]
    lines += [f"- `{nodeid}` - {reason}" for nodeid, reason in _skipped_tests]
    lines.append("")
    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write("\n".join(lines))
