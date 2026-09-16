# taxcalc-ai/src/taxcalc_ai/scripts/assert_langsmith_run_visible.py
"""Assert that a retrieval actually arrived in LangSmith - SaaS-side, not by reading source.

Run as ``python -m taxcalc_ai.scripts.assert_langsmith_run_visible``; exits non-zero if no
matching run is found. A ``LANGSMITH_API_KEY`` that can see the project is the only thing the
operator has to supply - the script brings its own database (see below), so the documented
one-line invocation works on a clean shell rather than only inside the CI step that used to wrap
it.

**Why this exists rather than a grep for ``@traceable``.** A grep proves the decorator is
written in the file. It does not prove a single trace ever left the process, and every realistic
way this breaks leaves the decorator exactly where it was:

* The API key is valid but belongs to a different workspace, so runs upload to a project nobody
  is looking at.
* ``LANGSMITH_PROJECT`` is misspelled, so a project is created on demand and the traces land in
  it - the upload succeeds and the dashboard the engineer opens stays empty.
* The process exits before the background uploader flushes, so short-lived jobs - CI steps,
  Lambdas, anything that finishes fast - trace nothing while long-running services trace fine.
* ``LANGSMITH_TRACING`` is unset or ``"false"``, so ``@traceable`` becomes a no-op passthrough -
  which is its documented behaviour, not a bug, and is invisible at the call site.

Each of those is a green build and a silent observability gap. The only check that covers them
is to fire a real retrieval and then ask LangSmith whether it can see it, which is what this
does.

The third of those is also why this script calls ``flush()`` and then polls: the LangSmith SDK
batches uploads on a background thread, so a query issued immediately after the traced call
reliably returns nothing.

The fourth is handled by ``setdefault`` rather than by requiring the caller to get it right.
Both ``LANGSMITH_PROJECT`` and ``LANGSMITH_TRACING`` are defaulted to values that make this
check meaningful, and an explicitly-set value always wins - so CI pinning
``LANGSMITH_TRACING=false`` still fails the gate, while a developer with nothing but a key in
their shell gets a run that proves something instead of a confusing red. Whatever the effective
values turn out to be, they are printed: a gate that silently repaired its own environment would
be lying about what it verified.

**The database is provisioned here, not by the caller.** The retrieval this fires has to hit a
real pgvector corpus, and the corpus has to exist before the call. Leaving that to the caller
meant the check only ran correctly inside a bespoke CI wrapper that started a container, applied
the DDL, embedded the seed corpus and exported the DSN - about thirty lines of YAML that no
developer could run and that nothing tested. It now lives here, in Python, exercised by the same
command in both places. ``TAXCALC_AI_PG_DSN`` still wins when it is set, for the case where the
corpus of interest is a real one rather than a throwaway.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import psycopg
from langsmith import Client
from langsmith.utils import LangSmithError

from taxcalc_ai.corpus import embed_dataframe, load_corpus
from taxcalc_ai.pgvector_loader import DSN_ENV_VAR, load_rows
from taxcalc_ai.rag import RETRIEVER_RUN_NAME, retrieve_chunks

#: How long to keep asking LangSmith before giving up. Generous because the failure this guards
#: is "the flush had not happened yet", and a too-short poll reproduces exactly that bug in the
#: checker itself - a flaky gate that everyone learns to re-run.
_POLL_ATTEMPTS: Final[int] = 20
_POLL_INTERVAL_SECONDS: Final[float] = 3.0

#: Only look at runs from the last few minutes, so a run left over from a previous CI job cannot
#: make this pass for a build whose own tracing is broken.
_LOOKBACK: Final[timedelta] = timedelta(minutes=10)

#: The project this gate uploads to and reads back. Deliberately NOT the ``taxcalc-ai-dev``
#: default that :class:`~taxcalc_ai.settings.TaxcalcAiSettings` carries: this runs on every push
#: and would otherwise bury the handful of traces an engineer is actually reading under CI
#: volume. The two are separate on purpose, which is also why it is defaulted here rather than
#: read from that settings object.
_DEFAULT_PROJECT: Final[str] = "taxcalc-ai-dev-ci"

#: Same image the test suite's fixture uses. Pinned to the major version rather than ``latest``
#: so a gate failure is never "the base image changed under us".
_PG_IMAGE: Final[str] = "pgvector/pgvector:pg16"

#: ``src/taxcalc_ai/scripts/`` -> package -> ``src/`` -> the project root.
_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

#: Read rather than duplicated. An inline copy of the schema in this file would drift from the
#: migration the moment either changed, and the gate would then be verifying retrieval against a
#: table shape nothing else uses.
_DDL_PATH: Final[Path] = _PROJECT_ROOT / "sql" / "V001__doc_chunks.sql"

#: The same synthetic corpus the retrieval tests load. The question below is answerable from it,
#: which matters only for the chunk count this prints - the gate's verdict depends on the run
#: reaching LangSmith, not on what came back.
_SEED_PATH: Final[Path] = _PROJECT_ROOT / "tests" / "fixtures" / "corpus_seed.jsonl"

_SAMPLE_QUESTION: Final[str] = "What is the standard deduction?"


def _configure_tracing() -> str:
    """Default the two tracing variables if unset, and return the project to search.

    ``setdefault`` for both: an explicit value from CI or from an operator's shell always wins,
    so this cannot paper over a deliberate ``LANGSMITH_TRACING=false``. See the module docstring
    for why defaulting them at all is the right call for this particular script.

    :returns: The LangSmith project name that the traced call will upload to - which is, by
        construction, the same one the query below reads. Deriving both from one value is the
        point: a checker that uploaded to one project and searched another would fail in a way
        that looks exactly like broken tracing.
    """
    project = os.environ.setdefault("LANGSMITH_PROJECT", _DEFAULT_PROJECT)
    tracing = os.environ.setdefault("LANGSMITH_TRACING", "true")
    print(f"tracing: LANGSMITH_TRACING={tracing!r} LANGSMITH_PROJECT={project!r}")
    return project


def _await_ready(dsn: str, attempts: int = 60, delay_seconds: float = 0.5) -> None:
    """Block until the container accepts a real connection, not just a TCP handshake.

    The official Postgres entrypoint starts a temporary server for ``initdb``, shuts it down,
    then starts the real one, so a port that was open a moment ago refuses the next connection.
    Waiting on a successful ``SELECT 1`` is the only check that spans that gap.

    ``tests/conftest.py`` has the same helper, and the duplication is deliberate: this module
    ships in the installed package and must not import the test tree to run.

    :raises RuntimeError: if the database never becomes reachable.
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
    raise RuntimeError(f"postgres never became ready at {dsn}") from last


def _seed(dsn: str) -> int:
    """Apply the sidecar's DDL and load the synthetic seed corpus.

    :returns: The number of chunk rows written.
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL_PATH.read_text())
        conn.commit()

    # No model is passed, so embed_dataframe loads its own. That is a second copy of the same
    # ~80 MB of weights this module already holds via taxcalc_ai.rag, and it is accepted rather
    # than optimised away: reaching into rag's private module-level model to save a few seconds
    # in a CI gate would make a private detail of the retrieval path load-bearing for a script.
    rows = load_rows(dsn, embed_dataframe(load_corpus(_SEED_PATH)))
    print(f"seeded {rows} chunk(s) into a throwaway {_PG_IMAGE} container")
    return rows


@contextmanager
def _corpus_dsn() -> Iterator[str]:
    """Yield a DSN for a database holding a retrievable corpus.

    Uses ``TAXCALC_AI_PG_DSN`` when it is set - the escape hatch for pointing the gate at a real
    corpus - and otherwise starts, seeds and disposes of a throwaway pgvector container.

    :raises RuntimeError: if no DSN is set and Testcontainers is not installed, since the script
        then has no way to obtain a database and reporting that as "no run visible" would blame
        tracing for a missing dependency.
    """
    configured = os.environ.get(DSN_ENV_VAR)
    if configured:
        print(f"using the corpus at {DSN_ENV_VAR} rather than starting a container")
        yield configured
        return

    # Imported lazily and locally: testcontainers is a dev-group dependency, and this module is
    # part of the installed package. A top-level import would make `import taxcalc_ai.scripts`
    # fail in any environment that installed the project without its test tooling.
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError as exc:  # pragma: no cover - exercised only on a prod-only install
        raise RuntimeError(
            f"{DSN_ENV_VAR} is unset and testcontainers is not installed; either export a DSN "
            "or install the dev dependency group"
        ) from exc

    with PostgresContainer(_PG_IMAGE) as pg:
        # get_connection_url() returns a SQLAlchemy-style postgresql+psycopg2:// URL; psycopg3
        # does not understand the driver suffix.
        dsn = pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        _await_ready(dsn)
        _seed(dsn)
        yield dsn


def _visible_run_count(client: Client, project: str, started_after: datetime) -> int:
    """Count runs named :data:`RETRIEVER_RUN_NAME` in ``project`` since ``started_after``."""
    return len(
        list(
            client.list_runs(
                project_name=project,
                filter=f'eq(name, "{RETRIEVER_RUN_NAME}")',
                start_time=started_after,
                limit=5,
            )
        )
    )


def main() -> int:
    """Fire one traced retrieval against a seeded corpus and confirm LangSmith can see the run.

    :returns: 0 when a matching run is visible, 1 otherwise. Written as a status code rather
        than an exception so the CI step's failure reads as "the gate failed" instead of as a
        Python traceback.
    """
    project = _configure_tracing()
    started_after = datetime.now(UTC) - _LOOKBACK

    with _corpus_dsn() as dsn:
        # A real call through the real decorated function. Deliberately not a synthetic
        # `@traceable` defined here: this must exercise the same code path production uses, or
        # it tests the SDK rather than this service's wiring. Inside the context manager, so the
        # container is still alive for the query - the trace is uploaded during this call.
        results = retrieve_chunks(dsn, _SAMPLE_QUESTION, k=3)
        print(f"issued one traced retrieval; {len(results)} chunks returned")

        client = Client()
        # Force the background uploader to drain before asking whether the run exists. Without
        # this the first poll is a coin flip and the whole check becomes flaky.
        client.flush()

        for attempt in range(1, _POLL_ATTEMPTS + 1):
            # "LangSmith cannot be reached or refuses the key" and "LangSmith has no such run"
            # are different verdicts and are reported as such. Observed, not hypothesised: on a
            # laptop behind a TLS-inspecting proxy this raises SSLError through
            # LangSmithConnectionError, and as an unhandled traceback that reads like the gate
            # found a tracing bug. It did not - it never got to look. Only the first line is
            # printed because the SDK appends the request URL and a masked key to the message,
            # which is noise in a CI log and, in the key's case, noise nobody should be training
            # themselves to read.
            try:
                found = _visible_run_count(client, project, started_after)
            except LangSmithError as exc:
                detail = str(exc).splitlines()[0]
                print(
                    f"FAIL: could not query LangSmith project {project!r} - "
                    f"{type(exc).__name__}: {detail}"
                )
                print(
                    "      This is a reachability or credential failure, NOT a verdict on "
                    "whether the retrieval was traced."
                )
                return 1
            if found:
                print(
                    f"OK: {found} run(s) named {RETRIEVER_RUN_NAME!r} visible in project "
                    f"{project!r}"
                )
                return 0
            print(f"  attempt {attempt}/{_POLL_ATTEMPTS}: no run visible yet, waiting...")
            time.sleep(_POLL_INTERVAL_SECONDS)

    print(
        f"FAIL: no run named {RETRIEVER_RUN_NAME!r} appeared in LangSmith project {project!r} "
        f"within {_POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS:.0f}s. Check that LANGSMITH_API_KEY "
        f"belongs to the workspace owning that project and that the project name matches "
        f"exactly."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
