# taxcalc-ai/src/taxcalc_ai/scripts/assert_langsmith_run_visible.py
"""Assert that a retrieval actually arrived in LangSmith - SaaS-side, not by reading source.

Run as ``python -m taxcalc_ai.scripts.assert_langsmith_run_visible``; exits non-zero if no
matching run is found.

**Why this exists rather than a grep for ``@traceable``.** A grep proves the decorator is
written in the file. It does not prove a single trace ever left the process, and every realistic
way this breaks leaves the decorator exactly where it was:

* ``LANGSMITH_TRACING`` is unset or ``"false"``, so ``@traceable`` becomes a no-op passthrough -
  which is its documented behaviour, not a bug, and is invisible at the call site.
* The API key is valid but belongs to a different workspace, so runs upload to a project nobody
  is looking at.
* ``LANGSMITH_PROJECT`` is misspelled, so a project is created on demand and the traces land in
  it - the upload succeeds and the dashboard the engineer opens stays empty.
* The process exits before the background uploader flushes, so short-lived jobs - CI steps,
  Lambdas, anything that finishes fast - trace nothing while long-running services trace fine.

Each of those is a green build and a silent observability gap. The only check that covers them
is to fire a real retrieval and then ask LangSmith whether it can see it, which is what this
does.

The last of those failure modes is also why this script calls ``flush()`` and then polls: the
LangSmith SDK batches uploads on a background thread, so a query issued immediately after the
traced call reliably returns nothing.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Final

from langsmith import Client

from taxcalc_ai.pgvector_loader import DSN_ENV_VAR
from taxcalc_ai.rag import RETRIEVER_RUN_NAME, retrieve_chunks

#: How long to keep asking LangSmith before giving up. Generous because the failure this guards
#: is "the flush had not happened yet", and a too-short poll reproduces exactly that bug in the
#: checker itself - a flaky gate that everyone learns to re-run.
_POLL_ATTEMPTS: Final[int] = 20
_POLL_INTERVAL_SECONDS: Final[float] = 3.0

#: Only look at runs from the last few minutes, so a run left over from a previous CI job cannot
#: make this pass for a build whose own tracing is broken.
_LOOKBACK: Final[timedelta] = timedelta(minutes=10)


def _project_name() -> str:
    """The LangSmith project to search; the same one the traced call uploads to."""
    return os.environ.get("LANGSMITH_PROJECT", "taxcalc-ai-dev")


def main() -> int:
    """Fire one traced retrieval, flush, and confirm LangSmith can see the run.

    :returns: 0 when a matching run is visible, 1 otherwise. Written as a status code rather
        than an exception so the CI step's failure reads as "the gate failed" instead of as a
        Python traceback.
    """
    project = _project_name()
    dsn = os.environ.get(DSN_ENV_VAR)
    if not dsn:
        print(f"FAIL: {DSN_ENV_VAR} is not set; cannot issue a traced retrieval")
        return 1

    started_after = datetime.now(UTC) - _LOOKBACK

    # A real call through the real decorated function. Deliberately not a synthetic
    # `@traceable` defined here: this must exercise the same code path production uses, or it
    # tests the SDK rather than this service's wiring.
    results = retrieve_chunks(dsn, "What is the standard deduction?", k=3)
    print(f"issued one traced retrieval; {len(results)} chunks returned")

    client = Client()
    # Force the background uploader to drain before asking whether the run exists. Without this
    # the first poll is a coin flip and the whole check becomes flaky.
    client.flush()

    for attempt in range(1, _POLL_ATTEMPTS + 1):
        runs = list(
            client.list_runs(
                project_name=project,
                filter=f'eq(name, "{RETRIEVER_RUN_NAME}")',
                start_time=started_after,
                limit=5,
            )
        )
        if runs:
            print(
                f"OK: {len(runs)} run(s) named {RETRIEVER_RUN_NAME!r} visible in project "
                f"{project!r} (most recent id {runs[0].id})"
            )
            return 0
        print(f"  attempt {attempt}/{_POLL_ATTEMPTS}: no run visible yet, waiting...")
        time.sleep(_POLL_INTERVAL_SECONDS)

    print(
        f"FAIL: no run named {RETRIEVER_RUN_NAME!r} appeared in LangSmith project {project!r} "
        f"within {_POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS:.0f}s. Check that LANGSMITH_TRACING "
        f"is 'true', that LANGSMITH_API_KEY belongs to the workspace owning that project, and "
        f"that the project name matches exactly."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
