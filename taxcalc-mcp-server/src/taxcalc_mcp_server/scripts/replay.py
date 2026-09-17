# taxcalc-mcp-server/src/taxcalc_mcp_server/scripts/replay.py
"""Replay recorded tool calls in-process and report per-tool p50/p95/p99.

**What this measures, and what it deliberately does not.** Every fixture carries its own canned
upstream response, and the harness swaps a stub in for the shared HTTP client before replaying.
So the numbers below are *this server's* cost - argument validation, schema enforcement, the
outbound request build, the response re-shape - and not the order service's latency or the
network's. That is the only thing a per-PR gate can honestly hold a developer to: a change here
cannot make the network faster, and a regression gate that includes the network fires on other
teams' deploys until people stop reading it.

**Why percentiles and not a mean.** The mean hides the shape. A tool that answers in 1 ms
ninety-nine times and 400 ms once has a mean of 5 ms and a p99 of 400 ms, and it is the p99 that
a caller waiting on a tool loop actually experiences.

**Why the p95 comparison is a ratio against the previous run and not an absolute budget.** An
absolute threshold in milliseconds is a number about the CI runner, not about the code: it goes
red when GitHub changes instance types and green when they change back, and within two such
cycles nobody believes it. A 15% regression against the previous run on the same tier is a
statement about the diff.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import anyio
import httpx

from taxcalc_mcp_server.app import AppCtx, enforce_strict_tool_schemas, mcp
from taxcalc_mcp_server.settings import Settings
from taxcalc_mcp_server.tools import _resources, llm, orders, rag  # noqa: F401 - registration

#: Where the report is written. Consumed by the CI step that compares runs.
DEFAULT_REPORT: Final[Path] = Path(".replay/latest.json")

#: Fraction by which a tool's p95 may regress before the merge-to-main tier fails. See the module
#: docstring for why this is a ratio rather than a millisecond budget.
P95_REGRESSION_LIMIT: Final[float] = 0.15


@dataclass(frozen=True)
class Fixture:
    """One recorded call and the upstream reply it should be replayed against.

    :ivar name: The fixture file's stem, used in the report.
    :ivar tool: Which tool to invoke.
    :ivar arguments: The ``tools/call`` arguments, exactly as a client would send them.
    :ivar upstream_status: Status the stubbed upstream returns.
    :ivar upstream_body: Body the stubbed upstream returns.
    :ivar rag_result: Canned pipeline result, for ``rag.retrieve_and_generate`` fixtures.
    """

    name: str
    tool: str
    arguments: dict[str, Any]
    upstream_status: int
    upstream_body: dict[str, Any]
    rag_result: dict[str, Any] | None


def load_fixtures(directory: Path) -> list[Fixture]:
    """Read every ``*.json`` fixture in ``directory``, sorted by name.

    Sorted so two runs replay in the same order; an unordered walk makes run-to-run comparison
    depend on filesystem iteration order, which is exactly the kind of variance a latency gate
    must not have.

    :param directory: Where the fixtures live.
    :returns: The fixtures.
    :raises FileNotFoundError: if the directory holds no fixtures. An empty set would otherwise
        produce a green gate that measured nothing - the failure mode this whole script exists
        to avoid elsewhere.
    """
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"no *.json fixtures in {directory}")
    fixtures: list[Fixture] = []
    for path in paths:
        raw = json.loads(path.read_text())
        fixtures.append(
            Fixture(
                name=path.stem,
                tool=raw["tool"],
                arguments=raw["arguments"],
                upstream_status=raw.get("upstream", {}).get("status", 200),
                upstream_body=raw.get("upstream", {}).get("body", {}),
                rag_result=raw.get("rag_result"),
            )
        )
    return fixtures


def _stub_transport(fixture: Fixture) -> httpx.MockTransport:
    """Build a transport that answers every request with ``fixture``'s canned reply.

    :param fixture: The fixture being replayed.
    :returns: A mock transport.
    """

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(fixture.upstream_status, json=fixture.upstream_body)

    return httpx.MockTransport(handler)


@contextmanager
def _request_context(fixture: Fixture, settings: Settings) -> Iterator[None]:
    """Install a request context carrying a stubbed :class:`AppCtx` for one replay.

    The handlers reach their dependencies through the MCP request context, so replaying a call
    outside a live session means providing one. Doing it this way - rather than calling the
    handler functions directly - is the point: the replay goes through the real dispatch, the
    real argument validation and the real schema enforcement, so a change that breaks any of
    them shows up here rather than only in production.

    :param fixture: The fixture being replayed.
    :param settings: Configuration for the stubbed context.
    :yields: Nothing; the context is active for the block.
    """
    from mcp.server.lowlevel.server import request_ctx
    from mcp.shared.context import RequestContext

    client = httpx.AsyncClient(
        base_url=settings.normalised_orders_url(), transport=_stub_transport(fixture)
    )

    def fake_rag(*_: object, **__: object) -> dict[str, Any]:
        return fixture.rag_result or {}

    app_ctx = AppCtx(http=client, rag_fn=fake_rag, settings=settings)
    token = request_ctx.set(
        RequestContext(
            request_id="replay",
            meta=None,
            session=None,  # type: ignore[arg-type]  # no handler on this path touches the session
            lifespan_context=app_ctx,
        )
    )
    try:
        yield
    finally:
        request_ctx.reset(token)


async def _replay_once(fixture: Fixture, settings: Settings) -> float:
    """Replay one fixture and return its elapsed milliseconds.

    :param fixture: The call to replay.
    :param settings: Configuration.
    :returns: Elapsed milliseconds.
    :raises RuntimeError: if the tool call errored. A latency report over failed calls is
        meaningless - failures are usually fast - so an error stops the run rather than
        contributing a flatteringly low sample.
    """
    with _request_context(fixture, settings):
        started = time.monotonic()
        try:
            await mcp.call_tool(fixture.tool, fixture.arguments)
        except Exception as exc:
            raise RuntimeError(f"fixture {fixture.name!r} ({fixture.tool}) failed: {exc}") from exc
        return (time.monotonic() - started) * 1000.0


def _percentiles(samples: Sequence[float]) -> dict[str, float]:
    """Return p50/p95/p99 for ``samples``, rounded to three decimal places.

    Nearest-rank on a sorted copy rather than an interpolating estimator: with the small sample
    counts a fixture set produces, interpolation reports a p99 that is not any observed value,
    which makes "which call was the p99" unanswerable.

    :param samples: Elapsed times in milliseconds.
    :returns: The three percentiles.
    """
    ordered = sorted(samples)

    def pick(q: float) -> float:
        index = max(0, min(len(ordered) - 1, round(q * len(ordered) + 0.5) - 1))
        return round(ordered[index], 3)

    return {"p50": pick(0.50), "p95": pick(0.95), "p99": pick(0.99)}


async def _run(fixtures: list[Fixture], repeats: int) -> dict[str, Any]:
    """Replay every fixture ``repeats`` times and build the report.

    :param fixtures: The fixtures to replay.
    :param repeats: How many times to replay each. More than one because a single sample per
        fixture makes a p95 a synonym for "the slowest call", which is dominated by whatever the
        interpreter happened to be doing.
    :returns: The report.
    """
    settings = Settings()
    per_tool: dict[str, list[float]] = {}
    for fixture in fixtures:
        for _ in range(repeats):
            per_tool.setdefault(fixture.tool, []).append(await _replay_once(fixture, settings))
    return {
        "fixtures": len(fixtures),
        "repeats": repeats,
        "tools": {
            tool: {"samples": len(samples), **_percentiles(samples)}
            for tool, samples in sorted(per_tool.items())
        },
    }


def compare(current: dict[str, Any], previous: dict[str, Any]) -> list[str]:
    """Return one message per tool whose p95 regressed beyond :data:`P95_REGRESSION_LIMIT`.

    A tool present in ``current`` but not in ``previous`` is not a regression - it is a new tool,
    and failing a build for adding one would make the gate an argument against new tools.

    :param current: This run's report.
    :param previous: The previous run's report.
    :returns: Human-readable regression messages; empty when nothing regressed.
    """
    messages: list[str] = []
    for tool, stats in current.get("tools", {}).items():
        before = previous.get("tools", {}).get(tool)
        if before is None:
            continue
        baseline = float(before["p95"])
        now = float(stats["p95"])
        # A baseline at or below zero cannot be a denominator, and a sub-millisecond baseline
        # makes the ratio a measure of timer resolution rather than of the change.
        if baseline <= 0.0:
            continue
        growth = (now - baseline) / baseline
        if growth > P95_REGRESSION_LIMIT:
            messages.append(
                f"{tool}: p95 {baseline:.3f}ms -> {now:.3f}ms (+{growth:.1%}, "
                f"limit +{P95_REGRESSION_LIMIT:.0%})"
            )
    return messages


def main(argv: Sequence[str] | None = None) -> int:
    """Run the replay and, on the merge tier, compare against the previous report.

    :param argv: Command-line arguments; ``sys.argv[1:]`` when omitted.
    :returns: Process exit status - 0 green, 1 on a replay failure or a p95 regression.
    """
    parser = argparse.ArgumentParser(description="Replay recorded MCP tool calls and time them.")
    parser.add_argument(
        "--fixtures", type=Path, required=True, help="Directory of *.json fixtures."
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Where to write JSON.")
    parser.add_argument("--repeats", type=int, default=20, help="Replays per fixture.")
    parser.add_argument(
        "--compare-to",
        type=Path,
        default=None,
        help="Previous report to compare p95 against; absent or missing means no comparison.",
    )
    args = parser.parse_args(argv)

    enforce_strict_tool_schemas(mcp)
    fixtures = load_fixtures(args.fixtures)
    try:
        report = anyio.run(_run, fixtures, args.repeats)
    except RuntimeError as exc:
        print(f"REPLAY FAILED: {exc}")
        return 1

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"replayed {report['fixtures']} fixtures x{report['repeats']} -> {args.report}")
    for tool, stats in report["tools"].items():
        print(f"  {tool:<28} p50={stats['p50']:>8.3f}ms p95={stats['p95']:>8.3f}ms "
              f"p99={stats['p99']:>8.3f}ms  n={stats['samples']}")

    if args.compare_to is None or not args.compare_to.exists():
        # Said out loud rather than passed over in silence. A gate that skips without naming the
        # reason is indistinguishable from a gate that ran and found nothing - the W7 D3 RAGAS
        # skip taught this repo that lesson once already.
        print(
            "p95 comparison skipped: no previous report at "
            f"{args.compare_to or '<not requested>'} (expected on the first run of this gate)"
        )
        return 0

    regressions = compare(report, json.loads(args.compare_to.read_text()))
    if regressions:
        print("P95 REGRESSION:")
        for message in regressions:
            print(f"  {message}")
        return 1
    print(f"p95 within +{P95_REGRESSION_LIMIT:.0%} of the previous run for every tool")
    return 0


if __name__ == "__main__":
    sys.exit(main())
