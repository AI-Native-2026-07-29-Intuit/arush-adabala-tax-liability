# taxcalc-agent-svc/src/taxcalc_agent_svc/scripts/eval.py
"""The CI gate: run the twenty-row suite and fail the build on any of three regressions.

``uv run python -m taxcalc_agent_svc.scripts.eval --gate``

Exits 1 when any of the following holds, and prints which one - the verdict names the metric, the
measured value and the floor, because a gate that only says "failed" costs whoever reads it a
local re-run to find out what broke:

* trajectory match below 0.70 - a routing regression,
* RAGAS faithfulness below 0.85 - an answer-quality regression,
* cost-per-run more than 15% above the committed baseline - the change that improves both of the
  above by spending three times as much.

**An unmeasured metric is a failure, not a pass.** If RAGAS cannot run - no evaluator
credential, a rate limit - faithfulness comes back as ``None`` and this gate *fails*, naming the
cause. That is the W7 D3 lesson paid for once already in this repository: a skip that renders as
a green tick is indistinguishable from a measurement that passed, and the metric silently stops
being protected. ``--allow-unmeasured-faithfulness`` is the deliberate escape hatch for the one
context where it is correct - a fork PR with no access to secrets - and it prints a loud banner
saying the gate was declared rather than measured.

``--offline`` substitutes stub node bodies. **The trajectory half of this gate is fully real in
offline mode**, because routing is decided by the supervisor and the supervisor is production
code: all twenty rows exercise the real keyword table, the real ``list[Send]`` fan-out plan and
the real reducers. Only the answers are canned, which is why faithfulness is not scored there -
grading a canned answer would be grading the stub.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from taxcalc_agent_svc.settings import Settings

# The eval harness lives in `evals/`, beside `src/` rather than inside it: it is CI surface, not
# shipped library code, and packaging it into the wheel would ship twenty scenarios and a RAGAS
# dependency to every consumer of this service.
#
# Loaded by path, and deliberately through `importlib` rather than a plain `from trajectory
# import ...`. A top-level import would have to sit textually AFTER the sys.path line, which puts
# it out of import-block order - and the formatter reorders it back on the next `ruff check
# --fix`, moving the import above the path setup and turning the gate into a ModuleNotFoundError.
# That is not hypothetical; it happened once here. An explicit import_module cannot be reordered
# into breaking, because the dependency is a statement sequence rather than a block layout.
_EVALS_DIR = Path(__file__).resolve().parents[3] / "evals"
if str(_EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(_EVALS_DIR))

_trajectory = importlib.import_module("trajectory")

#: Re-exported so this module's readers see the thresholds it applies without a second hop, and
#: so there is exactly ONE definition of each - in trajectory.py, next to the suite they gate.
TRAJECTORY_FLOOR: float = _trajectory.TRAJECTORY_FLOOR
FAITHFULNESS_FLOOR: float = _trajectory.FAITHFULNESS_FLOOR
COST_REGRESSION_LIMIT: float = _trajectory.COST_REGRESSION_LIMIT
run_eval = _trajectory.run_eval


def _install_offline_nodes() -> None:
    """Rebind the three node implementations to deterministic stubs.

    The answers are canned but the ROUTING is not: the supervisor, the fan-out plan, the
    reducers, the decorators and the deadlines are all production code. See the module docstring.

    :returns: Nothing; the rebinding is a module-level side effect.
    """
    from taxcalc_agent_svc.nodes import api, retrieval, synthesis

    async def fake_retrieval(state: Any, config: Any, settings: Any) -> dict[str, Any]:
        """Canned retrieval that echoes the question, so answer assertions mean something.

        :returns: A partial state carrying ``docs``.
        """
        return {
            "docs": [{"chunk_id": "chunk-doc-1-p0", "doc_id": "doc-1", "score": 0.9}],
            "cost_usd_e5": 120,
            "visited_nodes": ["retrieval_agent"],
        }

    async def fake_api(state: Any, config: Any, settings: Any) -> dict[str, Any]:
        """Canned tool result.

        :returns: A partial state carrying ``tool_results``.
        """
        return {
            "tool_results": {"orders.get_order": {"id": "ord-synth-9001"}},
            "cost_usd_e5": 200,
            "visited_nodes": ["api_agent"],
        }

    async def fake_synthesis(state: Any, config: Any, settings: Any) -> dict[str, Any]:
        """Canned answer that restates the question.

        Restating rather than returning a fixed string: the suite's
        ``expected_answer_substring`` is drawn from each question's own vocabulary, so an echo
        keeps that reported rate meaningful as a wiring check - it proves the question reached
        synthesis at all - without pretending to be an answer-quality measurement.

        :returns: A partial state carrying ``answer``.
        """
        payload = {
            "text": f"offline: {state.get('question', '')}",
            "citations": [],
            "confidence": 0.9,
        }
        return {
            "answer": json.dumps(payload),
            "cost_usd_e5": 300,
            "visited_nodes": ["synthesis_agent"],
        }

    retrieval._retrieval = fake_retrieval  # noqa: SLF001 - see the module docstring
    api._api = fake_api  # noqa: SLF001
    synthesis._synthesis = fake_synthesis  # noqa: SLF001


def verdict(summary: dict[str, Any], allow_unmeasured: bool) -> tuple[bool, list[str]]:
    """Apply the three gates to one run's summary.

    Pure, and separated from the run for exactly that reason: the thresholds and the arithmetic
    are unit-tested against synthetic summaries rather than only exercised by a red build, which
    is the difference between a gate that is known to work and one that is hoped to.

    :param summary: The output of :func:`trajectory.run_eval`.
    :param allow_unmeasured: Treat an unmeasured faithfulness as a pass, loudly.
    :returns: ``(passed, failures)`` - the failures naming metric, value and floor.
    """
    failures: list[str] = []

    traj = float(summary["trajectory_match"])
    if traj < TRAJECTORY_FLOOR:
        failures.append(f"trajectory match {traj:.3f} < floor {TRAJECTORY_FLOOR:.2f}")

    faith = summary["faithfulness"]
    # NaN is treated exactly as None. trajectory.py already converts it, and this is the second
    # line of defence: `float("nan") < 0.85` is False, so a NaN that reached here by any other
    # route - a hand-edited last_run.json, a future caller, a RAGAS shape not yet seen - would
    # pass this gate silently. A metric that is not a number was not measured.
    if faith is not None and math.isnan(float(faith)):
        faith = None
    if faith is None:
        if not allow_unmeasured:
            failures.append(
                "RAGAS faithfulness was NOT MEASURED (no evaluator credential, or nothing "
                "gradeable). An unmeasured metric is not a passing one - pass "
                "--allow-unmeasured-faithfulness to waive this deliberately."
            )
    elif float(faith) < FAITHFULNESS_FLOOR:
        failures.append(f"faithfulness {float(faith):.3f} < floor {FAITHFULNESS_FLOOR:.2f}")

    reg = float(summary["cost_regression"])
    if reg > COST_REGRESSION_LIMIT:
        failures.append(
            f"cost per run regressed {reg:.1%} (> {COST_REGRESSION_LIMIT:.0%}): "
            f"{summary['baseline_cost_usd_e5']} -> {summary['mean_cost_usd_e5']} (1e-5 USD)"
        )

    return not failures, failures


def _report(summary: dict[str, Any], allow_unmeasured: bool) -> int:
    """Print the summary and the verdict.

    :param summary: The run summary.
    :param allow_unmeasured: Whether an unmeasured faithfulness was waived.
    :returns: The process exit code.
    """
    faith = summary["faithfulness"]
    print(f"scenarios            : {summary['scenarios']}")
    print(f"trajectory match     : {summary['trajectory_match']:.3f}  (floor {TRAJECTORY_FLOOR})")
    print(f"answer substring rate: {summary['answer_substring_rate']:.3f}  (reported, not gated)")
    print(
        "faithfulness         : "
        + (f"{float(faith):.3f}  (floor {FAITHFULNESS_FLOOR})" if faith is not None
           else "NOT MEASURED")
    )
    print(
        f"mean cost per run    : {summary['mean_cost_usd_e5']} (1e-5 USD), "
        f"baseline {summary['baseline_cost_usd_e5']}, "
        f"regression {summary['cost_regression']:+.1%} (limit {COST_REGRESSION_LIMIT:.0%})"
    )

    errors = [r for r in summary["rows"] if r.get("error")]
    if errors:
        # Printed before the mismatches and separately from them: twenty rows that all failed to
        # RUN is a different diagnosis from twenty rows that ran and routed wrongly, and a reader
        # looking at a wall of MISMATCH lines would reach the second conclusion.
        print(f"\n{len(errors)} of {summary['scenarios']} scenarios FAILED TO RUN:")
        for row in errors[:3]:
            print(f"  {row['qid']}: {row['error'][:160]}")
        if len(errors) > 3:
            print(f"  ... and {len(errors) - 3} more with the same or similar cause")

    for row in summary["rows"]:
        if row["match"] != 1.0 and not row.get("error"):
            print(
                f"  MISMATCH {row['qid']}: "
                f"expected {row['expected']} but visited {row['visited']}"
            )

    if faith is None and allow_unmeasured:
        print(
            "\n*** WARNING: faithfulness was DECLARED, not MEASURED. This gate did not check "
            "answer quality on this run. ***"
        )

    passed, failures = verdict(summary, allow_unmeasured)
    if passed:
        print("\nGATE PASSED")
        return 0
    print("\nGATE FAILED:")
    for f in failures:
        print(f"  - {f}")
    return 1


async def _run(offline: bool, gate: bool, allow_unmeasured: bool) -> int:
    """Build the graph, run the suite, and report.

    :param offline: Substitute stub node bodies.
    :param gate: Exit non-zero on a regression. Without it the suite is measured and reported but
        never fails, which is what a developer wants when iterating on a prompt.
    :param allow_unmeasured: Waive an unmeasured faithfulness.
    :returns: The process exit code.
    """
    if offline:
        _install_offline_nodes()

    from taxcalc_agent_svc.graph import build_taxcalc_agent_graph

    settings = Settings()
    graph, closer = await build_taxcalc_agent_graph(settings)
    try:
        summary = await run_eval(graph, settings, score_faithfulness=not offline)
    finally:
        await closer.__aexit__(None, None, None)

    code = _report(summary, allow_unmeasured or offline)
    return code if gate else 0


def main() -> int:
    """Parse arguments and run the gate.

    :returns: The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gate", action="store_true", help="exit 1 on any regression")
    ap.add_argument(
        "--offline",
        action="store_true",
        help="stub the node bodies; routing stays real, faithfulness is not scored",
    )
    ap.add_argument(
        "--allow-unmeasured-faithfulness",
        action="store_true",
        help="treat an unmeasured faithfulness as a pass, loudly (fork PRs with no secrets)",
    )
    args = ap.parse_args()
    return asyncio.run(_run(args.offline, args.gate, args.allow_unmeasured_faithfulness))


if __name__ == "__main__":
    sys.exit(main())
