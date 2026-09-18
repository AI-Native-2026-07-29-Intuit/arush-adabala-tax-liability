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

**Why a ratio alone is not enough, and what the warmup and the noise floor are for.** These
handlers answer in a few hundred *microseconds* - there is no network in the measurement - and at
that magnitude a 15% ratio is inside the run-to-run noise. Two consecutive runs of this script on
an idle laptop, with no code change between them, reported +18% and +44%. A gate that fires on
that is worse than no gate: it trains people to re-run the build until it goes green, and then a
real regression goes green too. Two things make the comparison mean something:

*A warmup pass per fixture, discarded.* The first call to a tool pays for imports, Pydantic's
validator construction and the first pass through httpx's transport stack. With twenty samples
that one-off cost *is* the p95 - which is why ``orders.create_refund`` was reporting a p95 of
12-56 ms against a median of 0.26 ms. Timing only warm calls is what makes the tail a property of
the handler rather than of the interpreter's startup.

*A noise floor, applied to the pair.* A change that leaves a call under a millisecond is not a
latency regression any caller can perceive, and the ratio between two sub-millisecond numbers is
mostly timer resolution. So a comparison is skipped only when *both* sides are under
:data:`NOISE_FLOOR_MS` - if the new number crosses the floor, it is compared no matter how small
the baseline was, because 0.3 ms to 3 ms is exactly the regression this gate exists to catch.
The skips are listed rather than passed over, so a run where nothing could be compared says so.
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

#: Below this, in milliseconds, a change is noise rather than a regression. Applied to the
#: baseline and the current value together - see the module docstring. One millisecond is chosen
#: because it is roughly where these in-process handlers stop being dominated by interpreter
#: jitter, and because no consumer of a tool call can perceive a change that stays under it.
NOISE_FLOOR_MS: Final[float] = 1.0

#: Untimed calls per fixture before sampling starts. One is enough: the costs being excluded are
#: paid exactly once per tool, on its first call.
DEFAULT_WARMUP: Final[int] = 1


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


async def _run(fixtures: list[Fixture], repeats: int, warmup: int) -> dict[str, Any]:
    """Replay every fixture ``repeats`` times and build the report.

    :param fixtures: The fixtures to replay.
    :param repeats: How many times to replay each. More than one because a single sample per
        fixture makes a p95 a synonym for "the slowest call", which is dominated by whatever the
        interpreter happened to be doing.
    :param warmup: Untimed calls per fixture before sampling. See the module docstring: the
        first call to a tool pays one-off costs that, in a twenty-sample run, become the p95.
    :returns: The report.
    """
    settings = Settings()
    per_tool: dict[str, list[float]] = {}
    for fixture in fixtures:
        for _ in range(warmup):
            # Awaited for its side effects only - the elapsed time is deliberately dropped. A
            # failure still raises, so a fixture that cannot replay fails here rather than
            # producing a report built from a warmup that silently errored.
            await _replay_once(fixture, settings)
        for _ in range(repeats):
            per_tool.setdefault(fixture.tool, []).append(await _replay_once(fixture, settings))
    return {
        "fixtures": len(fixtures),
        "repeats": repeats,
        "warmup": warmup,
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
    return [v.message for v in _verdicts(current, previous) if v.regressed]


@dataclass(frozen=True)
class Verdict:
    """What the gate concluded about one tool.

    Three states, not two, and the third is the one worth naming: a comparison that was
    deliberately not made. A gate that skipped every tool and a gate that compared every tool and
    found nothing both exit 0, and an operator has to be able to tell those apart - so the state
    is carried as data rather than inferred from whether a message string is empty.

    :ivar tool: The tool compared.
    :ivar regressed: True when this is a failure the build should stop on.
    :ivar skipped: True when no comparison was made, for the reason in :attr:`message`.
    :ivar message: Human-readable detail; empty only for a clean pass.
    """

    tool: str
    regressed: bool
    skipped: bool
    message: str


def _verdicts(current: dict[str, Any], previous: dict[str, Any]) -> list[Verdict]:
    """Return one :class:`Verdict` per tool that had a baseline to compare against.

    :param current: This run's report.
    :param previous: The previous run's report.
    :returns: The verdicts, in report order.
    """
    verdicts: list[Verdict] = []
    for tool, stats in current.get("tools", {}).items():
        before = previous.get("tools", {}).get(tool)
        if before is None:
            continue
        baseline = float(before["p95"])
        now = float(stats["p95"])
        # A baseline at or below zero cannot be a denominator.
        if baseline <= 0.0:
            verdicts.append(
                Verdict(tool, False, True, f"{tool}: SKIPPED, baseline p95 is {baseline:.3f}ms")
            )
            continue
        # Both sides under the floor: the ratio between two sub-millisecond numbers is timer
        # resolution, not a change anyone can feel. A `now` that crosses the floor is compared
        # however small the baseline was - see the module docstring.
        if max(baseline, now) < NOISE_FLOOR_MS:
            verdicts.append(
                Verdict(
                    tool,
                    False,
                    True,
                    f"{tool}: SKIPPED, p95 {baseline:.3f}ms -> {now:.3f}ms, both under the "
                    f"{NOISE_FLOOR_MS:.0f}ms noise floor",
                )
            )
            continue
        growth = (now - baseline) / baseline
        if growth > P95_REGRESSION_LIMIT:
            verdicts.append(
                Verdict(
                    tool,
                    True,
                    False,
                    f"{tool}: p95 {baseline:.3f}ms -> {now:.3f}ms (+{growth:.1%}, "
                    f"limit +{P95_REGRESSION_LIMIT:.0%})",
                )
            )
        else:
            verdicts.append(Verdict(tool, False, False, ""))
    return verdicts


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
    parser.add_argument("--repeats", type=int, default=20, help="Timed replays per fixture.")
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP,
        help="Untimed replays per fixture before sampling starts.",
    )
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
        report = anyio.run(_run, fixtures, args.repeats, args.warmup)
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

    verdicts = _verdicts(report, json.loads(args.compare_to.read_text()))
    regressions = [v.message for v in verdicts if v.regressed]
    skipped = [v.message for v in verdicts if v.skipped]
    # Printed on both outcomes, and before the verdict: a reader has to be able to see WHICH
    # tools the green tick covers. A gate that skipped every tool is reported as such rather
    # than as a pass.
    for message in skipped:
        print(f"  {message}")
    if regressions:
        print("P95 REGRESSION:")
        for message in regressions:
            print(f"  {message}")
        return 1
    compared = len(verdicts) - len(skipped)
    if compared == 0:
        print(
            f"p95 comparison covered NO tools: all {len(verdicts)} are below the "
            f"{NOISE_FLOOR_MS:.0f}ms noise floor. Nothing regressed, and nothing was proven."
        )
        return 0
    print(
        f"p95 within +{P95_REGRESSION_LIMIT:.0%} of the previous run for "
        f"{compared} of {len(verdicts)} tools ({len(skipped)} under the noise floor)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
