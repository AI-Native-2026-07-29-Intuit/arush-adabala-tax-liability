# taxcalc-ai/tests/test_assert_langsmith_run_visible.py
"""Tests for the CI gate that proves a trace reached LangSmith.

What can be tested locally is the gate's *wiring*: which project it searches, that it does not
overwrite an operator's explicit configuration, that it prefers a configured corpus over
starting a container, and that "no run visible" really does exit non-zero. What cannot be tested
locally is the thing the script exists to check - whether a run actually arrived at the SaaS -
and no attempt is made to fake it here. A stubbed LangSmith client that reports a run would
assert nothing about LangSmith; it asserts that the poll loop returns 0 when told a run exists,
which is a different and much smaller claim, and is written below as exactly that.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import ClassVar, cast

import pytest
from langsmith import Client
from langsmith.utils import LangSmithConnectionError

from taxcalc_ai.pgvector_loader import DSN_ENV_VAR
from taxcalc_ai.rag import RETRIEVER_RUN_NAME
from taxcalc_ai.scripts import assert_langsmith_run_visible as gate

TRACING_VARS = ("LANGSMITH_PROJECT", "LANGSMITH_TRACING")


class _StubClient:
    """Stands in for ``langsmith.Client`` so ``main()`` can run without network or credentials.

    Records whether ``flush()`` was called, because the flush is the fix for the background
    uploader race and deleting it would leave every other assertion here passing, and records
    the query arguments so the run-name filter can be checked without a workspace.
    """

    flushed: bool = False
    last_query: ClassVar[dict[str, object]] = {}

    def flush(self) -> None:
        _StubClient.flushed = True

    def list_runs(self, **kwargs: object) -> list[object]:
        _StubClient.last_query = dict(kwargs)
        return []


def test_the_ci_project_is_the_default_and_tracing_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean shell gets a configuration under which this check means something.

    The default is the CI project rather than ``taxcalc-ai-dev``: this gate runs on every push,
    and mixing that volume into the project an engineer reads while debugging is how a trace
    view stops being useful.
    """
    for name in TRACING_VARS:
        monkeypatch.delenv(name, raising=False)

    assert gate._configure_tracing() == "taxcalc-ai-dev-ci"

    assert os.environ["LANGSMITH_PROJECT"] == "taxcalc-ai-dev-ci"
    # Without this the decorator is a documented no-op and the gate would fail on a clean shell
    # for a reason that has nothing to do with the wiring it is checking.
    assert os.environ["LANGSMITH_TRACING"] == "true"


def test_explicit_tracing_configuration_is_never_overwritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator - or CI - that sets these deliberately keeps them.

    This is what stops the defaulting above from hiding a real misconfiguration: a pipeline that
    pins ``LANGSMITH_TRACING=false`` still gets a red gate rather than a repaired one.
    """
    monkeypatch.setenv("LANGSMITH_PROJECT", "someone-elses-project")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")

    assert gate._configure_tracing() == "someone-elses-project"

    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_a_configured_dsn_is_used_instead_of_starting_a_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``TAXCALC_AI_PG_DSN`` wins, for pointing the gate at a real corpus.

    Also the reason this test is fast: reaching the container branch would pull an image.
    """
    monkeypatch.setenv(DSN_ENV_VAR, "postgresql://someone@example.invalid:5432/corpus")

    with gate._corpus_dsn() as dsn:
        assert dsn == "postgresql://someone@example.invalid:5432/corpus"


def _stub_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the two things ``main()`` does that need a database and a credential."""
    monkeypatch.setenv(DSN_ENV_VAR, "postgresql://someone@example.invalid:5432/corpus")
    monkeypatch.setattr(gate, "retrieve_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(gate, "Client", _StubClient)
    # The real interval would make the failure path below take a minute to assert one integer.
    monkeypatch.setattr(gate, "_POLL_ATTEMPTS", 2)
    monkeypatch.setattr(gate, "_POLL_INTERVAL_SECONDS", 0.0)


def test_exits_non_zero_when_no_run_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failing case is the one that matters: a silent observability gap must go red.

    A gate that cannot fail is worse than no gate, because it is also believed.
    """
    _stub_retrieval(monkeypatch)
    monkeypatch.setattr(gate, "_visible_run_count", lambda *args: 0)

    assert gate.main() == 1


def test_exits_zero_once_a_run_becomes_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """The poll loop returns as soon as the SaaS reports a run, and flushes before asking."""
    _stub_retrieval(monkeypatch)
    _StubClient.flushed = False

    seen: list[datetime] = []

    def _count(_client: object, _project: str, started_after: datetime) -> int:
        # Second call succeeds: the first is the pre-flush window the retry loop exists for.
        seen.append(started_after)
        return 0 if len(seen) == 1 else 1

    monkeypatch.setattr(gate, "_visible_run_count", _count)

    assert gate.main() == 0
    assert _StubClient.flushed, "the background uploader must be drained before the first poll"
    # The lookback is computed once, not per attempt - a window that slid forward on each poll
    # could step past the run it is looking for.
    assert len(set(seen)) == 1


def test_an_unreachable_langsmith_is_reported_as_such(monkeypatch: pytest.MonkeyPatch) -> None:
    """A query that never completed is not evidence that the retrieval went untraced.

    Both verdicts exit 1 - the gate has proved nothing either way - but they send whoever reads
    the log to different places, so they must not read alike. This is the case that actually
    happens on a laptop behind a TLS-inspecting proxy.
    """
    _stub_retrieval(monkeypatch)

    def _raise(*_args: object) -> int:
        raise LangSmithConnectionError("Connection error caused failure to GET /sessions\nnoise")

    monkeypatch.setattr(gate, "_visible_run_count", _raise)

    assert gate.main() == 1


def test_the_seed_and_ddl_it_provisions_from_are_present() -> None:
    """The paths the container branch reads are resolved from the package, and they exist.

    ``parents[3]`` is the kind of thing that breaks silently when a file moves, and it would
    break inside a CI step that has already spent a minute pulling an image.
    """
    assert gate._DDL_PATH.is_file(), gate._DDL_PATH
    assert gate._SEED_PATH.is_file(), gate._SEED_PATH


def test_the_query_asks_for_the_name_the_retrieval_publishes() -> None:
    """The run name is a contract between the decorator and this query, not a label.

    Asserted on the filter string the client is actually handed, rather than on the constant:
    renaming the constant in both places would keep a constant-comparison green, while a filter
    that no longer matches what ``@traceable`` publishes is precisely how this gate would start
    failing for a reason nobody could see.
    """
    count = gate._visible_run_count(cast(Client, _StubClient()), "some-project", datetime.now(UTC))

    assert count == 0
    assert _StubClient.last_query["filter"] == f'eq(name, "{RETRIEVER_RUN_NAME}")'
    assert _StubClient.last_query["project_name"] == "some-project"
    assert RETRIEVER_RUN_NAME == "taxcalc_ai.retrieve_chunks"


@pytest.fixture(autouse=True)
def _restore_tracing_env() -> Iterator[None]:
    """Undo the module-level ``setdefault`` calls these tests provoke.

    ``_configure_tracing`` writes to ``os.environ`` by design, and the suite's own conftest sets
    ``LANGSMITH_TRACING=false`` for every other test. Without this, test order would decide
    whether an unrelated test uploaded a run to a real project with a fake key.
    """
    before = {name: os.environ.get(name) for name in TRACING_VARS}
    yield
    for name, value in before.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
