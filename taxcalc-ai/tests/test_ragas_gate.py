# taxcalc-ai/tests/test_ragas_gate.py
"""The W7 D3 CI gate: faithfulness below :data:`FAITHFULNESS_GATE` is fatal; three floors.

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
    judge_failure_detail,
)

#: The fatal floor. Below this the build stops.
#:
#: **Re-baselined 2026-09-18 from 0.85, with the measurement that 0.85 never had.** W7 D3 set
#: 0.85 while `docs/ragas/w7d3.md` recorded, correctly, that nothing could be measured: the
#: evaluator workspace was at its usage limit, so every run of this gate skipped. The limit
#: lifted and the golden set scored 0.720 / 0.720 / 0.715 across three runs with zero
#: judging-job failures.
#:
#: 0.85 was not merely optimistic, it was **unreachable**: the committed fixture is deliberately
#: 30 clean + 20 broken, and only 36 of its 50 rows can be faithful by construction, so 36/50 =
#: 0.72 is the ceiling for any retrieval system. The full arithmetic is recorded on ``FLOORS`` in
#: ``test_ragas_thresholds.py``. 0.70 keeps this gate stricter than the 0.68 W7 D2 floor - which
#: ``test_the_gate_threshold_is_stricter_than_the_w7d2_floor`` still enforces - and trips on a
#: two-row regression (34/50 = 0.68).
#:
#: This is a *correction of an arithmetic error*, not a build being made green by loosening a
#: gate. The distinction is the evidence above, and it is why that evidence lives in the source
#: rather than in a commit message.
FAITHFULNESS_GATE: Final[float] = 0.70

#: The three diagnostic floors. Re-baselined 2026-09-18 alongside the gate, for the same reason
#: and from the same three measurements: answer_relevancy 0.596/0.599/0.602 and both context
#: metrics 0.720. The Topic 10 values (0.80 / 0.75 / 0.80) were set unmeasured and are above what
#: this fixture can produce.
#:
#: Each stays >= its W7 D2 counterpart, which the meta-test below enforces. Breaching one fails
#: this test without halting the step, so a run that breaches several reports all of them.
DIAGNOSTIC_FLOORS: Final[dict[str, float]] = {
    "answer_relevancy": 0.55,
    "context_precision": 0.70,
    "context_recall": 0.70,
}


@pytest.mark.slow
@pytest.mark.skipif(
    EVALUATOR_API_KEY is None,
    reason=(
        f"no evaluator credential: set {ANTHROPIC_KEY_ENV} or {PREFIXED_ANTHROPIC_KEY_ENV} in "
        "the environment, or either in .env (gitignored). A SKIP HERE MEANS THE FAITHFULNESS "
        "GATE DID NOT RUN - the threshold is declared, not measured. conftest.py re-reports "
        "this as a workflow annotation and a job-summary line precisely so that distinction "
        "survives into a reviewer's eyeline instead of rendering as a green check."
    ),
)
def test_ragas_faithfulness_gate() -> None:
    """Faithfulness under the gate raises ``SystemExit``; the other three are asserted floors.

    The NaN handling is not defensive padding. RAGAS's executor catches each judging job's
    exception itself, logs it at ERROR and writes NaN into that row's score - so a spend-capped
    or revoked evaluator does not raise at all, it returns a complete result whose every value
    is NaN. Left unhandled that reads as ``nan >= <gate>`` failing, which in a CI log is
    indistinguishable from a genuine quality regression and is the wrong thing to page someone
    about. ALL metrics NaN is a provisioning fact and is reported as a skip; SOME metrics NaN
    means the evaluator was reachable and something specific broke, which still fails.
    """
    try:
        scores, capture = _run_eval()
    # Broad, narrowed on the next line. Kept for the paths that DO propagate - an unusable key
    # can raise while the client is built, before any job is queued.
    except Exception as exc:
        underlying = _provisioning_failure(exc)
        if underlying is None:
            raise
        pytest.skip(_UNAVAILABLE.format(detail=f"{type(underlying).__name__}: {underlying}"))

    if scores and all(math.isnan(value) for value in scores.values()):
        # Carries the real underlying error, not just "nothing was judged" - see
        # test_ragas_thresholds._JudgeErrorCapture for why that distinction is the whole fix.
        pytest.skip(_UNAVAILABLE.format(detail=judge_failure_detail(capture)))

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
    gate that can fail fast everywhere. It asserts the RELATIONSHIP, not either value: the gate
    must stay stricter than the W7 D2 floor, whatever those two numbers are. That is what keeps
    "a red build may not be fixed by loosening a constant" enforceable while still allowing the
    pair to be re-baselined together on evidence - which is what happened on 2026-09-18, when
    both moved down (0.80 -> 0.68, 0.85 -> 0.70) because the committed fixture cannot score
    above 0.72. See ``FLOORS`` in test_ragas_thresholds.py for that arithmetic.
    """
    from .test_ragas_thresholds import FLOORS

    assert FLOORS["faithfulness"] < FAITHFULNESS_GATE, (
        f"the W7 D3 gate ({FAITHFULNESS_GATE}) must be stricter than the W7 D2 floor "
        f"({FLOORS['faithfulness']})"
    )
    # And the diagnostic floors are at or above their W7 D2 values, for the same reason.
    for metric, floor in DIAGNOSTIC_FLOORS.items():
        assert floor >= FLOORS[metric], (metric, floor, FLOORS[metric])


def test_the_all_nan_skip_reports_the_underlying_error_not_just_its_absence() -> None:
    """A dead evaluator produces a skip naming the real cause, deduplicated.

    Runs without credentials and without a network. RAGAS reports a dead evaluator as a complete
    result full of NaN rather than as an exception, so the all-NaN branch is the one that fires
    in CI - and until now it could say only *that* nothing was judged, never *why*. "Raise the
    spend limit", "rotate the key", "the model id is wrong" and "CI has no egress" are four
    different fixes behind one identical annotation.

    The deduplication matters as much as the capture: a fifty-row run against a dead evaluator
    logs ~200 identical records, and a skip reason that is two hundred copies of one line is less
    informative than one copy, because nobody reads to the end of it.
    """
    import logging

    from .test_ragas_thresholds import _capture_judge_errors, judge_failure_detail

    with _capture_judge_errors() as capture:
        ragas_log = logging.getLogger("ragas.executor")
        for _ in range(200):
            ragas_log.error("Exception raised in Job: AuthenticationError: invalid x-api-key")
        ragas_log.error("Exception raised in Job: BadRequestError: credit balance is too low")

    detail = judge_failure_detail(capture)

    assert "201 judging job(s) failed" in detail
    # Both distinct causes survive; the 200 duplicates collapse to one mention.
    assert "invalid x-api-key" in detail
    assert "credit balance is too low" in detail
    assert detail.count("invalid x-api-key") == 1, detail

    # The handler is detached on exit, so a later evaluation in the same session cannot
    # accumulate this one's records - and a module-level logger is not left holding it.
    assert capture not in logging.getLogger("ragas.executor").handlers

    # And the honest answer when RAGAS logged nothing at all: say the cause is unknown rather
    # than inventing one.
    from .test_ragas_thresholds import _JudgeErrorCapture

    assert "cause unknown" in judge_failure_detail(_JudgeErrorCapture())


#: The real record RAGAS emitted on run 35190597131, with the request_id that made 200 copies of
#: one cause compare unequal. Pinned verbatim because the normalisation is only worth anything if
#: it handles the payload actually observed, rather than a tidied-up version of it.
_REAL_CI_ERROR = (
    "Exception raised in Job[{index}]: AnthropicInvalidRequestError(Error code: 400 - "
    "{{'type': 'error', 'error': {{'type': 'invalid_request_error', 'message': 'You have "
    "reached your specified workspace API usage limits. You will regain access on 2026-10-01 "
    "at 00:00 UTC.'}}, 'request_id': 'req_011Cf8Z9{suffix}'}})"
)


def test_two_hundred_copies_of_one_cause_collapse_to_one_line() -> None:
    """The real CI payload deduplicates to a single cause, not "+197 other distinct errors".

    This is the bug the first version of the capture shipped with. Deduplication compared raw
    strings, and every Anthropic error carries its own ``request_id`` while every RAGAS record
    carries its own ``Job[n]`` index - so 200 instances of ONE usage-limit error were reported as
    200 distinct causes in a 4,000-character annotation. An annotation that says there are two
    hundred problems when there is one is not more informative than no annotation; it is a wall
    of text a reviewer scrolls past, which is exactly what the capture was added to prevent.

    Observed on run 35190597131, and pinned here with that run's own payload.
    """
    import logging

    from .test_ragas_thresholds import _capture_judge_errors, judge_failure_detail

    with _capture_judge_errors() as capture:
        log = logging.getLogger("ragas.executor")
        for index in range(200):
            log.error(_REAL_CI_ERROR.format(index=index, suffix=f"{index:04d}xyz"))

    detail = judge_failure_detail(capture)

    assert "200 judging job(s) failed" in detail
    # One cause, so no "+N other distinct causes" suffix at all.
    assert "other distinct causes" not in detail, detail
    # The actionable part survives normalisation: what went wrong, and when it clears.
    assert "workspace API usage limits" in detail
    assert "2026-10-01" in detail
    # The volatile fields are replaced rather than deleted, so a reader can still see that a
    # request id existed - they just cannot make two identical causes look different.
    assert "'request_id': '...'" in detail
    assert "Job[n]" in detail

    # Normalisation must not be so aggressive that a genuinely different failure is swallowed.
    with _capture_judge_errors() as mixed:
        log = logging.getLogger("ragas.executor")
        log.error(_REAL_CI_ERROR.format(index=1, suffix="0001abc"))
        log.error("Exception raised in Job[2]: AuthenticationError: invalid x-api-key")

    mixed_detail = judge_failure_detail(mixed)
    assert "workspace API usage limits" in mixed_detail
    assert "invalid x-api-key" in mixed_detail
