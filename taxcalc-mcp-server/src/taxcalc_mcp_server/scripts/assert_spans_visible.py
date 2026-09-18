# taxcalc-mcp-server/src/taxcalc_mcp_server/scripts/assert_spans_visible.py
"""Assert a tool span actually arrived in LangSmith - SaaS-side, not by reading source.

Run as ``python -m taxcalc_mcp_server.scripts.assert_spans_visible``. Exits non-zero when a key
is present and the span cannot be found; exits zero, loudly, when there is no key at all.

**Why this exists when ``tests/test_tracing.py`` already passes.** That test substitutes the
LangSmith client's transport and asserts the run this server *would* send - its name, its
project, that there is exactly one, that a failing call still produces one. It is the right test
for "the decorator is wired to the configured project", and it is worth having because it runs
offline on every PR. What it cannot prove is that a trace ever left the process. Every realistic
way that breaks leaves both the decorator and that test exactly as they are:

* The key is valid but belongs to a different workspace, so spans upload to a project nobody
  is looking at.
* ``TAXCALC_MCP_LANGSMITH_PROJECT`` is misspelled, so a project is created on demand and the
  spans land in it - the upload succeeds and the dashboard an engineer opens stays empty. Note
  the prefix: the bare ``LANGSMITH_PROJECT`` the SDK honours by default is *not* what moves this
  server's spans, which is a trap this script fell into once and now documents.
* The process exits before the background uploader flushes. This one bites *this* server harder
  than most: the stdio transport's whole lifetime can be a handful of tool calls, and a desktop
  client closing the pipe terminates it immediately.
* ``LANGSMITH_TRACING`` is unset or ``"false"``, so ``@traceable`` becomes a no-op passthrough -
  documented behaviour, not a bug, and invisible at the call site.

Each of those is a green build and a silent observability gap, which is why this runs in CI with
the real secret rather than being left as a claim in a README.

**The third is why this flushes and then polls.** The SDK batches uploads on a background
thread, so querying immediately after the traced call reliably returns nothing.

**The fourth is handled by ``setdefault``, and whatever the effective values turn out to be are
printed.** A developer with nothing but a key in their shell gets a run that proves something;
CI pinning ``LANGSMITH_TRACING=false`` still fails the gate. A check that silently repaired its
own environment would be lying about what it verified.

**It needs no database and no upstream.** Unlike the sidecar's equivalent, this fires
``orders.get_order`` against a stubbed transport, so the only thing an operator supplies is the
key. The span under test is produced by the same ``@traceable`` decorator on the same handler
that serves real traffic; what is stubbed is the HTTP call underneath it, which is not what is
being verified.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Final

import httpx

#: Tool whose span is looked for. The cheapest of the four - a single stubbed GET - and the one
#: with no credential, corpus or model behind it.
PROBE_TOOL: Final[str] = "orders.get_order"

#: How long to poll before declaring the span invisible. The uploader flushes on a background
#: thread; 30 s is far longer than that needs and short enough to fail a CI step, not hang it.
POLL_DEADLINE_S: Final[float] = 30.0

#: Gap between polls.
POLL_INTERVAL_S: Final[float] = 2.0

#: Clock skew allowance when filtering runs by start time. Without it a run created moments ago
#: can sort just outside a window computed from this machine's clock and be missed.
CLOCK_SKEW_S: Final[int] = 60


def _configure_tracing() -> tuple[str, str]:
    """Default the flag that decides whether tracing happens, resolve the project, report both.

    **The project is read from the same place the decorators read it, and that detail is the
    whole reason this function exists.** ``@traceable(project_name=...)`` is given
    :data:`taxcalc_mcp_server.app.TRACE_PROJECT`, which comes from
    :attr:`Settings.langsmith_project` - and ``Settings`` carries the ``TAXCALC_MCP_`` env
    prefix, so the variable that moves it is ``TAXCALC_MCP_LANGSMITH_PROJECT``. The *bare*
    ``LANGSMITH_PROJECT`` that the LangSmith SDK honours by default does **not**.

    An earlier version of this script read the bare variable. It would have fired spans into one
    project and then queried a different one, failing every time while reporting "the span never
    arrived" - a gate that manufactures the defect it claims to detect, which is worse than no
    gate. Deriving the project from ``TRACE_PROJECT`` means the query follows the spans wherever
    they actually went.

    ``setdefault`` on the flag rather than assignment: an explicitly-set value always wins, so CI
    pinning ``LANGSMITH_TRACING=false`` still fails this gate instead of being overridden.

    :returns: The effective project and tracing flag.
    """
    from taxcalc_mcp_server.app import TRACE_PROJECT

    os.environ.setdefault("LANGSMITH_TRACING", "true")
    return TRACE_PROJECT, os.environ["LANGSMITH_TRACING"]


def _fire_one_tool_call() -> None:
    """Invoke :data:`PROBE_TOOL` through the real MCP dispatch against a stubbed upstream.

    Imported inside the function because importing the app configures logging and builds the
    server; doing that at module scope would run it even when this script is about to skip.
    """
    from mcp.server.lowlevel.server import request_ctx
    from mcp.shared.context import RequestContext

    from taxcalc_mcp_server.app import AppCtx, enforce_strict_tool_schemas, mcp
    from taxcalc_mcp_server.settings import Settings
    from taxcalc_mcp_server.tools import _resources, llm, orders, rag  # noqa: F401 - registration

    enforce_strict_tool_schemas(mcp)
    settings = Settings()
    http = httpx.AsyncClient(
        base_url=settings.normalised_orders_url(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "order_id": "ord-synth-9001",
                    "tenant_id": "tenant-a",
                    "total": "42.50",
                    "status": "paid",
                },
            )
        ),
    )
    token = request_ctx.set(
        RequestContext(
            request_id="assert-spans-visible",
            meta=None,
            session=None,  # type: ignore[arg-type]  # this path never touches the session
            lifespan_context=AppCtx(
                http=http, rag_fn=lambda *_a, **_k: {}, settings=settings
            ),
        )
    )
    try:
        asyncio.run(
            mcp.call_tool(
                PROBE_TOOL, {"order_id": "ord-synth-9001", "tenant_id": "tenant-a"}
            )
        )
    finally:
        request_ctx.reset(token)


def _visible_span_count(project: str, started_after: datetime) -> tuple[int, str]:
    """Count runs named :data:`PROBE_TOOL` in ``project`` since ``started_after``.

    Returns the reachability problem rather than raising it. "LangSmith could not be reached or
    would not authenticate" and "LangSmith was reached and the span is not there" are different
    failures with different fixes - a key, a proxy, a firewall for the first; a project name or a
    missing flush for the second - and a traceback reports neither. It just says the gate itself
    fell over, which is the least useful thing a gate can say.

    :param project: LangSmith project to query.
    :param started_after: Lower bound on run start time.
    :returns: ``(count, problem)``. ``problem`` is ``""`` when the query succeeded; otherwise it
        describes why the answer is unknown and ``count`` is meaningless.
    """
    from langsmith import Client
    from langsmith.utils import LangSmithError

    try:
        client = Client()
        return (
            sum(
                1
                for _ in client.list_runs(
                    project_name=project,
                    start_time=started_after,
                    filter=f'eq(name, "{PROBE_TOOL}")',
                )
            ),
            "",
        )
    except LangSmithError as exc:
        # One line, not the traceback: the exception text already names the host, the status and
        # the masked key, which is what an operator needs.
        return 0, f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"


def main() -> int:
    """Fire one traced tool call and confirm LangSmith can see it.

    :returns: ``0`` when the span is visible, or when there is no key to check with; ``1`` when a
        key is present and the span never appeared.
    """
    if not os.environ.get("LANGSMITH_API_KEY"):
        # Named, not silent. A skip that says only "skipped" is indistinguishable from a check
        # that ran and found nothing - the failure mode this repository has already been bitten
        # by once, in the RAGAS gate.
        print(
            "SKIPPED: LANGSMITH_API_KEY is unset, so span DELIVERY is declared, not measured.\n"
            "         tests/test_tracing.py still proves each tool emits one run, named after\n"
            "         the tool, into the configured project - offline. What is unverified here\n"
            "         is only that the upload reaches LangSmith and lands where it should.\n"
            "         Set LANGSMITH_API_KEY to turn this into a real check."
        )
        return 0

    project, tracing = _configure_tracing()
    print(f"project={project!r} tracing={tracing!r} tool={PROBE_TOOL!r}")
    if tracing.strip().lower() in {"0", "false", "no", "off"}:
        print(
            f"FAILED: LANGSMITH_TRACING={tracing!r} makes @traceable a no-op, so no span can "
            "arrive. This is the gate working, not a misconfiguration of the gate."
        )
        return 1

    # Computed before the call, minus the skew allowance: a window that starts after the run does
    # excludes the very run being looked for.
    started_after = datetime.now(UTC) - timedelta(seconds=CLOCK_SKEW_S)
    _fire_one_tool_call()

    from langsmith import Client
    from langsmith.utils import LangSmithError

    # The uploader batches on a background thread; without this the first poll races it.
    try:
        Client().flush()
    except LangSmithError as exc:
        print(f"FAILED: could not flush traces to LangSmith - {type(exc).__name__}: {exc}")
        return 1

    deadline = time.monotonic() + POLL_DEADLINE_S
    problem = ""
    while time.monotonic() < deadline:
        count, problem = _visible_span_count(project, started_after)
        if problem:
            break
        if count > 0:
            print(f"OK: {count} run(s) named {PROBE_TOOL!r} visible in project {project!r}")
            return 0
        time.sleep(POLL_INTERVAL_S)

    if problem:
        print(
            f"FAILED: LangSmith could not be queried, so span delivery is UNKNOWN rather than\n"
            f"        absent: {problem}\n"
            "        Fix the reachability first - a bad or unscoped API key, an intercepting\n"
            "        proxy, or no egress. This is not evidence that tracing is broken."
        )
        return 1

    print(
        f"FAILED: LangSmith was reachable but no run named {PROBE_TOOL!r} became visible in\n"
        f"        project {project!r} within {POLL_DEADLINE_S:.0f}s.\n"
        "        The decorator is wired - tests/test_tracing.py proves that offline - so the\n"
        "        cause is delivery: a key scoped to another workspace, a project name that does\n"
        "        not match, or a process exiting before the uploader flushed."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
