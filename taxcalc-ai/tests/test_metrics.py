# taxcalc-ai/tests/test_metrics.py
"""The rerank timeout counters: both increment, the ratio holds, and the series is scrapeable.

No model and no database here - :func:`~taxcalc_ai.metrics.record_rerank` is the seam
:func:`~taxcalc_ai.rerank.bge_rerank` calls, and testing it directly means these assertions run
in milliseconds instead of behind a 1.1 GB model load. That the wiring exists at all is asserted
in tests/test_rerank.py, on the real reranker, where the timeout path is forced.

Counters are process-global by design (see the module docstring), so these tests read deltas
rather than absolute values: another test in the same session may have reranked first.
"""

from __future__ import annotations

from taxcalc_ai.metrics import (
    METRICS_CONTENT_TYPE,
    RERANK_REQUESTS_COUNTER,
    RERANK_TIMEOUT_COUNTER,
    record_rerank,
    render_metrics,
)
from taxcalc_ai.rerank import RERANK_TIMEOUT_ATTRIBUTE


def _counts() -> tuple[float, float]:
    """Current ``(requests, timeouts)`` totals."""
    return (
        RERANK_REQUESTS_COUNTER._value.get(),
        RERANK_TIMEOUT_COUNTER._value.get(),
    )


def test_a_breach_increments_both_the_numerator_and_the_denominator() -> None:
    """A timeout counts as a timeout AND as a request, so the alert ratio cannot exceed 1.

    This is the invariant ``record_rerank`` exists to hold. Two counters incremented by two
    separate call sites would eventually drift, and the failure is silent: the alert expression
    still evaluates, it just returns a number that means nothing.
    """
    requests_before, timeouts_before = _counts()

    record_rerank(timed_out=True)

    requests_after, timeouts_after = _counts()
    assert requests_after - requests_before == 1
    assert timeouts_after - timeouts_before == 1


def test_a_completed_rerank_moves_only_the_denominator() -> None:
    """The happy path is counted too - otherwise there is no traffic figure to divide by."""
    requests_before, timeouts_before = _counts()

    record_rerank(timed_out=False)

    requests_after, timeouts_after = _counts()
    assert requests_after - requests_before == 1
    assert timeouts_after - timeouts_before == 0


def test_the_counter_is_exported_under_the_name_the_alert_rule_uses() -> None:
    """The series reaches the exposition payload as ``rerank_timeout_total``.

    The name is the contract: an alert rule is a string referring to a series, so a renamed or
    unregistered metric is a silently dead alert rather than a broken build. ``prometheus_client``
    appends ``_total`` to a counter, so the family name shared with the LangSmith span attribute
    is asserted here alongside the sample name an SRE actually writes.
    """
    record_rerank(timed_out=True)
    payload = render_metrics().decode("utf-8")

    assert f"{RERANK_TIMEOUT_ATTRIBUTE}_total" in payload
    assert "rerank_requests_total" in payload
    # The HELP line is what a human reads in a dashboard's metric picker; an unhelped metric is
    # technically scrapeable and practically undiscoverable.
    assert f"# HELP {RERANK_TIMEOUT_ATTRIBUTE}_total" in payload

    # Prometheus rejects a payload served as text/plain without the version parameter, so the
    # content type a caller must set is re-exported rather than left to be remembered.
    assert METRICS_CONTENT_TYPE.startswith("text/plain")
    assert "version=" in METRICS_CONTENT_TYPE
