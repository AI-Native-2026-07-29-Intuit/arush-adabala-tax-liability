# taxcalc-ai/src/taxcalc_ai/metrics.py
"""Prometheus counters for the failure modes this pipeline handles by degrading rather than raising.

There is exactly one thing here that could not be done with a log line, and it is the reason the
module exists. :func:`~taxcalc_ai.rerank.bge_rerank` fails SOFT: when the cross-encoder overruns
its 300 ms budget the request still returns usable context, so nothing errors, nothing retries,
and no HTTP status changes. The only externally visible difference is that answers are ordered by
retrieval score instead of by cross-encoder score - a quality regression with no signal attached
to it. An unreported soft failure is worse than a hard one, because a hard one pages somebody.

**Why a counter and not the LangSmith span attribute alone.** ``bge_rerank`` also writes
``rerank_timeout`` into its span metadata, and that is the right tool for *debugging one request*
- it sits next to the trace showing which chunks were returned in which order. It is the wrong
tool for *alerting*: LangSmith sampling is a tracing decision, the project is not on the paging
path, and an alert rule that depends on a tracing backend being both complete and reachable is an
alert rule that goes quiet exactly when things are bad. A counter in the serving process is
scraped on the SRE's own schedule and survives tracing being switched off entirely. The two are
kept deliberately: the span answers "what happened to this request", the counter answers "how
often is this happening".

**Two counters, because a numerator alone cannot be alerted on.** ``rerank_timeout_total`` rising
says nothing by itself - ten breaches an hour is a different situation at ten requests an hour
than at ten thousand. ``rerank_requests_total`` is the denominator that turns it into a rate, so
the alert is a ratio over a window::

    rate(rerank_timeout_total[10m]) / rate(rerank_requests_total[10m]) > 0.5

which is the shape that stays meaningful across a traffic change. Note the expected reading on
CPU-only hardware is near 1.0 by design (see :mod:`taxcalc_ai.rerank`), so this threshold is
a statement about a GPU-backed deployment; on CPU the ratio IS the signal that the stage needs
one.

**Registered on the default registry, and that is a choice with a cost.** It means a scrape
endpoint gets these for free and needs no wiring - but it also means importing this module twice
under two names, or reloading it, raises ``Duplicated timeseries``. That is acceptable for a
process-wide serving metric and is why the counters are module-level constants rather than
anything constructed per call. Tests that need isolation read
:func:`render_metrics` or the counters' own ``_value``, they do not re-register.

Nothing here starts a server. :func:`start_metrics_server` exists for a process that has no HTTP
surface of its own (an Airflow worker, a CLI batch run); a process that does own an HTTP server
should serve :func:`render_metrics` on its own ``/metrics`` route instead of opening a second
listener on a second port.
"""

from __future__ import annotations

import logging
from typing import Final

from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest, start_http_server

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.metrics")

#: Breaches of the rerank latency budget. ``prometheus_client`` appends ``_total`` to a counter's
#: sample name, so the exported series is ``rerank_timeout_total`` while the metric family - the
#: name an SRE writes in a rule and the name the LangSmith span attribute uses - is
#: ``rerank_timeout``. Same identifier on both signals, on purpose.
RERANK_TIMEOUT_COUNTER: Final[Counter] = Counter(
    "rerank_timeout",
    "Reranker calls that exceeded their latency budget and fell back to retrieval order.",
)

#: Every rerank attempt, breach or not - the denominator above. Incremented on the same paths as
#: the counter above so the ratio cannot drift: a timeout increments both, never just one.
RERANK_REQUESTS_COUNTER: Final[Counter] = Counter(
    "rerank_requests",
    "Reranker calls attempted, whether or not they completed inside the latency budget.",
)

#: Media type a scrape endpoint must set. Re-exported so a caller serving :func:`render_metrics`
#: on its own route does not have to import ``prometheus_client`` to get the header right - a
#: text/plain default makes Prometheus reject the payload.
METRICS_CONTENT_TYPE: Final[str] = CONTENT_TYPE_LATEST


def record_rerank(timed_out: bool) -> None:
    """Record one rerank attempt and whether it breached its budget.

    A single function rather than two exported counters the caller increments itself, so the
    invariant that every breach also counts as a request holds in one place. Getting that wrong
    silently corrupts the alert ratio rather than breaking anything visible.

    :param timed_out: Whether the attempt fell back to retrieval order.
    """
    RERANK_REQUESTS_COUNTER.inc()
    if timed_out:
        RERANK_TIMEOUT_COUNTER.inc()


def render_metrics() -> bytes:
    """Return the default registry in Prometheus text exposition format.

    For a process that already owns an HTTP server: serve these bytes on ``/metrics`` with
    :data:`METRICS_CONTENT_TYPE` as the content type.

    :returns: The exposition payload, encoded UTF-8.
    """
    return generate_latest()


def start_metrics_server(port: int = 9_090) -> None:
    """Open a scrape endpoint on ``port`` for a process with no HTTP surface of its own.

    Starts a daemon thread, so it does not keep the process alive and does not need shutting
    down. Not called at import: a process decides whether it is long-lived enough to be scraped.

    :param port: TCP port to listen on.
    """
    start_http_server(port)
    _LOG.info(
        "metrics.server.started",
        extra={"event": "metrics.server.started", "port": port},
    )
