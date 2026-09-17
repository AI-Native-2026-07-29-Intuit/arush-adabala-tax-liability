# taxcalc-mcp-server/src/taxcalc_mcp_server/observability.py
"""One structured span per tool call, on stderr, next to the LangSmith trace.

**Why both this and ``@traceable``.** They answer different questions for different people.
LangSmith holds the *semantic* trace - the prompt, the retrieved chunks, the answer - and an
engineer debugging a bad answer reads that. These lines hold the *operational* trace - which
tool, which tenant, how long, what it cost, which error code - and they are what the Grafana
dashboard aggregates and what an on-call engineer greps at 3am without a LangSmith login.
Keeping both is not duplication: dropping the structured line would put operational monitoring
behind a vendor's availability, and dropping the span would leave the semantic detail nowhere.

**Why a context manager rather than a pair of log calls.** Because the ``end`` line has to be
emitted on *every* exit path, including the ones nobody remembers - a raised
:class:`McpError`, a timeout, a cancellation when the client hangs up mid-call. Hand-written
pairs go missing on exactly those paths, which are the paths worth measuring: a dashboard built
from lines that only appear on success reports a system that never fails.

**Why ``duration_ms`` is measured with a monotonic clock.** Wall-clock time can step backwards
over an NTP correction, and a negative latency in a percentile calculation is not a slow request,
it is a corrupted p99 for the whole window.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import McpError

from taxcalc_mcp_server.app import log


@asynccontextmanager
async def observe(tool: str, tenant_id: str) -> AsyncIterator[dict[str, object]]:
    """Emit ``tool.invoke.start`` and ``tool.invoke.end`` around one tool call.

    :param tool: The tool name, exactly as published in ``tools/list`` - so a dashboard can be
        joined against the catalogue without a translation table.
    :param tenant_id: The tenant the call acts on.
    :yields: A mutable dict of extra fields for the ``end`` line. Handlers add what only they
        know - ``cost_usd_minor`` from a proxy response header, ``citations`` from a retrieval -
        and anything left unset simply does not appear rather than appearing as a misleading zero.
    :raises McpError: re-raised unchanged after the code has been recorded. This context manager
        observes; it does not handle. Swallowing an error here would turn a failed tool call into
        a successful one returning ``None``, which the caller would then act on.
    """
    extra: dict[str, object] = {}
    log.info("tool.invoke.start", tool=tool, tenant_id=tenant_id)
    started = time.monotonic()
    try:
        yield extra
    except McpError as exc:
        log.info(
            "tool.invoke.end",
            tool=tool,
            tenant_id=tenant_id,
            duration_ms=_elapsed_ms(started),
            mcp_error_code=exc.error.code,
            **extra,
        )
        raise
    except Exception as exc:
        # An unmapped exception is a bug in THIS server, not an upstream condition. It is logged
        # with its type so the stderr line names the defect, and re-raised so the SDK still
        # returns a protocol-level error to the caller rather than hanging the call.
        log.info(
            "tool.invoke.end",
            tool=tool,
            tenant_id=tenant_id,
            duration_ms=_elapsed_ms(started),
            error_type=type(exc).__name__,
            **extra,
        )
        raise
    else:
        log.info(
            "tool.invoke.end",
            tool=tool,
            tenant_id=tenant_id,
            duration_ms=_elapsed_ms(started),
            **extra,
        )


def _elapsed_ms(started: float) -> int:
    """Return whole milliseconds since ``started``.

    An integer because these values are bucketed into latency histograms, and a float there buys
    sub-millisecond precision that no consumer reads while making every value a distinct
    cardinality key.

    :param started: A :func:`time.monotonic` reading.
    :returns: Elapsed milliseconds.
    """
    return int((time.monotonic() - started) * 1000)
