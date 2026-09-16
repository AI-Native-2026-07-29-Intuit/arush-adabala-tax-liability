# taxcalc-ai/tests/test_ragas_gate.py
"""The W7 D3 CI gate: faithfulness >= 0.85 is fatal, the other three metrics are floors.

W7 D2 committed four floors and asserted all of them the same way. Today one of them is
promoted to a *gate* and the asymmetry is the point.

**Why faithfulness is the gate metric and ``context_precision`` is not.** Faithfulness measures
whether the answer's claims are supported by the retrieved context - so a faithfulness
regression means the system is stating things the corpus does not say, to a user, in a tax
product. That is the user-facing failure. The other three diagnose *why*:
``context_precision`` and ``context_recall`` say the retrieval brought back the wrong or
too-little context, ``answer_relevancy`` says the answer drifted off the question. Gating on a
diagnostic would block a PR that improved the outcome while moving a diagnostic sideways - and
the four upgrades landed today do exactly that (see ``docs/ragas/w7d3.md``: MMR moves
``context_precision`` by +0.01 while lifting ``answer_relevancy`` by +0.02).

**``SystemExit``, not ``assert``, for the gate.** The distinction is about what a reader of the
CI log sees. An ``assert`` failure is one red test among however many ran; ``SystemExit`` with a
message carrying the measured score terminates the step and puts the number in the log's last
line. The three floors below stay plain asserts deliberately: they are warn-not-fail in the
sense that matters - they fail *this test* without halting the process, so a run that breaches
two of them reports both rather than stopping at the first.

Marked ``slow``: it makes real judging calls. CI does not opt out.
"""

from __future__ import annotations

import math
from typing import Final

import pytest

# Imported from the W7 D2 module rather than reimplemented. The credential resolution alone
# (process env under two names, then the gitignored .env under two names, rejecting the
# committed `replace-me` placeholder) is thirty lines of logic that must behave identically in
# both gates - a second copy is a second thing to get subtly wrong, and the failure mode is a
# gate that skips silently on a machine where the other one runs.
from .test_ragas_thresholds import (
    _UNAVAILABLE,
    ANTHROPIC_KEY_ENV,
    EVALUATOR_API_KEY,
    PREFIXED_ANTHROPIC_KEY_ENV,
    _provisioning_failure,
    _run_eval,
)

#: The fatal floor. Below this the build stops.
FAITHFULNESS_GATE: Final[float] = 0.85

#: The three diagnostic floors, at the Topic 10 values. Breaching one fails this test; it does
#: not halt the step, so a run that breaches several reports all of them.
DIAGNOSTIC_FLOORS: Final[dict[str, float]] = {
    "answer_relevancy": 0.80,
    "context_precision": 0.75,
    "context_recall": 0.80,
}


@pytest.mark.slow
@pytest.mark.skipif(
    EVALUATOR_API_KEY is None,
    reason=(
        f"no evaluator credential: set {ANTHROPIC_KEY_ENV} or {PREFIXED_ANTHROPIC_KEY_ENV} in "
        "the environment, or either in .env (gitignored). A SKIP HERE MEANS THE FAITHFULNESS "
        "GATE DID NOT RUN - the 0.85 threshold is declared, not measured. conftest.py re-reports "
        "this as a workflow annotation and a job-summary line precisely so that distinction "
        "survives into a reviewer's eyeline instead of rendering as a green check."
    ),
)
def test_ragas_faithfulness_gate() -> None:
    """``faithfulness < 0.85`` raises ``SystemExit``; the other three are asserted floors.

    The NaN handling is not defensive padding. RAGAS's executor catches each judging job's
    exception itself, logs it at ERROR and writes NaN into that row's score - so a spend-capped
    or revoked evaluator does not raise at all, it returns a complete result whose every value
    is NaN. Left unhandled that reads as ``nan >= 0.85`` failing, which in a CI log is
    indistinguishable from a genuine quality regression and is the wrong thing to page someone
    about. ALL metrics NaN is a provisioning fact and is reported as a skip; SOME metrics NaN
    means the evaluator was reachable and something specific broke, which still fails.
    """
    try:
        scores = _run_eval()
    # Broad, narrowed on the next line. Kept for the paths that DO propagate - an unusable key
    # can raise while the client is built, before any job is queued.
    except Exception as exc:
        underlying = _provisioning_failure(exc)
        if underlying is None:
            raise
        pytest.skip(_UNAVAILABLE.format(detail=f"{type(underlying).__name__}: {underlying}"))

    if scores and all(math.isnan(value) for value in scores.values()):
        pytest.skip(
            _UNAVAILABLE.format(
                detail="every metric returned NaN; RAGAS logged per-job errors at ERROR level"
            )
        )

    faithfulness = scores.get("faithfulness")
    if faithfulness is None or math.isnan(faithfulness):
        # Un-evaluated, not low. Raised rather than skipped: the other metrics came back, so the
        # evaluator was reachable and this one metric failing is a real defect.
        raise SystemExit(
            f"RAGAS faithfulness was not evaluated (scores={scores}); the gate cannot pass "
            "on an absent measurement"
        )
    if faithfulness < FAITHFULNESS_GATE:
        raise SystemExit(
            f"RAGAS faithfulness {faithfulness:.3f} below gate {FAITHFULNESS_GATE}. "
            f"The answer is stating things the corpus does not support. Full scores: {scores}"
        )

    # The other three are warn-not-fail per Topic 10: asserted, so a regression is visible, but
    # not fatal to the step - they diagnose the cause rather than being the user-facing failure.
    for metric, floor in DIAGNOSTIC_FLOORS.items():
        assert metric in scores, f"{metric} missing from RAGAS result: {scores}"
        assert not math.isnan(scores[metric]), (
            f"{metric} is NaN - it was not evaluated, rather than scoring low: {scores}"
        )
        assert scores[metric] >= floor, f"{metric} below the Topic 10 floor {floor}: {scores}"


def test_the_gate_threshold_is_stricter_than_the_w7d2_floor() -> None:
    """The gate may only ever move up, and this pins that it has.

    Runs without credentials and without a network, which is the point: it is the half of the
    gate that can fail fast everywhere. W7 D2 recorded ``faithfulness >= 0.80``; today's gate is
    0.85. A future day that "fixed" a red build by lowering this constant would fail here, which
    is the only mechanism that makes "thresholds tighten but never loosen" more than a comment.
    """
    from .test_ragas_thresholds import FLOORS

    assert FLOORS["faithfulness"] < FAITHFULNESS_GATE, (
        f"the W7 D3 gate ({FAITHFULNESS_GATE}) must be stricter than the W7 D2 floor "
        f"({FLOORS['faithfulness']})"
    )
    # And the diagnostic floors are at or above their W7 D2 values, for the same reason.
    for metric, floor in DIAGNOSTIC_FLOORS.items():
        assert floor >= FLOORS[metric], (metric, floor, FLOORS[metric])
