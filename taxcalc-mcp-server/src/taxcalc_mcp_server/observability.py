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

**Why ``cost_usd_minor`` is always present, and why it travels with a ``cost_source``.** A field
that appears only on the tools that happen to spend money cannot be aggregated: a dashboard
summing it has no way to tell "this tool cost nothing" from "this line predates the
instrumentation", and a missing key in a time series reads as a gap rather than as a zero. So
every ``end`` line carries the number. That alone would be misleading, though, because a zero
means two very different things here - ``orders.get_order`` spends nothing, while
``rag.retrieve_and_generate`` spends real money - and a total that treats the second as free is
wrong in the direction that matters. ``cost_source`` names which zero it is, so the dashboard
sums :data:`COST_SOURCE_PROXY` lines and counts :data:`COST_SOURCE_UNPRICED` ones as the known
blind spot rather than as spend that did not happen.

That blind spot is now narrower than it was, and for a reason worth recording: the W7 D3 sidecar
used not to report its generation cost at all, and :func:`taxcalc_ai.rag.retrieve_and_generate`
now returns a ``usage`` block carrying the call's ``input_tokens`` and ``output_tokens`` (absent
on a semantic-cache hit, where nothing was spent). **This server does not yet read it**, so
``rag.retrieve_and_generate`` still emits :data:`COST_SOURCE_UNPRICED` - the number is available
and simply not consumed here. Pricing it is a change to this module, not to the sidecar.

**Why the tenant cross-check lives here.** The bearer's ``tenant_id`` claim and the tenant a
tool was *asked* to act on are two different facts, and every tool has both. Comparing them in
each handler would be the same four lines written four times, with the fifth tool free to forget
them; comparing them here means a tool cannot be written that skips the check, because the
instrument every tool already goes through is what performs it. It is defence in depth and not
the authoritative check - that stays in the Java services that own the data - but refusing a
call this server can already see is cross-tenant is cheaper than forwarding it, and the refusal
is logged with the same ``mcp_error_code`` field as any other failure.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from mcp import McpError

from taxcalc_mcp_server.app import log
from taxcalc_mcp_server.errors import tenant_mismatch
from taxcalc_mcp_server.tenancy import cross_tenant_claim

#: ``cost_source`` for a tool that spends no LLM money at all - the two ``orders.*`` tools. Its
#: ``cost_usd_minor`` is a true zero.
COST_SOURCE_NONE: Final[str] = "none"

#: ``cost_source`` for a cost read from the proxy's ``X-Cost-Usd`` response header. The only
#: value whose ``cost_usd_minor`` may be summed into a spend total.
COST_SOURCE_PROXY: Final[str] = "proxy-header"

#: ``cost_source`` for a call that DID spend money this server cannot price. The W7 D3 sidecar
#: calls Anthropic directly and returns an answer with no usage block, so the generation's cost
#: is invisible here; reporting it as :data:`COST_SOURCE_NONE` would book real spend as free.
COST_SOURCE_UNPRICED: Final[str] = "unpriced"


@asynccontextmanager
async def observe(tool: str, tenant_id: str) -> AsyncIterator[dict[str, object]]:
    """Emit ``tool.invoke.start`` and ``tool.invoke.end`` around one tool call.

    :param tool: The tool name, exactly as published in ``tools/list`` - so a dashboard can be
        joined against the catalogue without a translation table.
    :param tenant_id: The tenant the call acts on.
    :yields: A mutable dict of extra fields for the ``end`` line, pre-seeded with
        ``cost_usd_minor`` and ``cost_source`` so those two are never absent. Handlers overwrite
        them when they know better and add what only they know - ``citations`` from a retrieval,
        ``http_status`` from a forwarded call.
    :raises McpError: 4030 when the validated bearer's tenant claim contradicts ``tenant_id``
        (see the module docstring), and any error a handler raises, re-raised unchanged after
        its code has been recorded. Beyond that one check this context manager observes; it does
        not handle. Swallowing a handler's error would turn a failed tool call into a successful
        one returning ``None``, which the caller would then act on.
    """
    extra: dict[str, object] = {
        "cost_usd_minor": 0,
        "cost_source": COST_SOURCE_NONE,
    }
    log.info("tool.invoke.start", tool=tool, tenant_id=tenant_id)
    started = time.monotonic()
    try:
        # Inside the try, so the refusal is reported by the same `end` line as any other
        # failure. Raised before the yield, so the handler's body never runs.
        claimed = cross_tenant_claim(tenant_id)
        if claimed:
            raise tenant_mismatch(claimed, tenant_id)
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
