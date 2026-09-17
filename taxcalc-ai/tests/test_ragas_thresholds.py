# taxcalc-ai/tests/test_ragas_thresholds.py
"""RAGAS golden-set baseline; thresholds tighten over the week but never loosen.

The floors asserted here are today's recorded baseline, not an aspiration. Their job is to make
a retrieval regression fail a build: W7 D3 changes the retrieval strategy, W7 D4 publishes it as
an MCP tool and W7 D5 drives it from a LangGraph flow, and each of those can degrade answer
quality without breaking a single type or raising a single exception. A threshold test is the
only gate that catches that class of change.

The golden set is deliberately not all clean. Thirty of its fifty rows are well-grounded; the
remaining twenty reproduce three of the failure modes from Topic 9 - missing context, junk
context, and near-duplicate context. A golden set on which every metric scores 1.0 has no
headroom to fall and therefore cannot detect anything; the floors below sit where they do
*because* those twenty rows drag the aggregate down by a known amount.

Marked ``slow`` so a developer can opt out locally (``-m "not slow"``). CI does not opt out.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

import anthropic
import pytest
from datasets import Dataset
from ragas import evaluate

# The metric CLASSES, imported from their concrete modules, rather than the module-level
# singletons (`from ragas.metrics import faithfulness`). Those singletons are deprecated as of
# ragas 0.4 and emit a DeprecationWarning on import, which this project's filterwarnings=error
# policy turns into a collection error. The classes are not deprecated, are instantiated here
# instead of being shared process-wide, and still satisfy `isinstance(..., Metric)` - which is
# what `evaluate()` requires. See the note on the ragas requirement in pyproject.toml.
from ragas.metrics._answer_relevance import AnswerRelevancy
from ragas.metrics._context_precision import ContextPrecision
from ragas.metrics._context_recall import ContextRecall
from ragas.metrics._faithfulness import Faithfulness
from ragas.run_config import RunConfig

from taxcalc_ai.corpus import MODEL_NAME

GOLDEN: Final[Path] = Path(__file__).resolve().parent / "golden" / "taxcalc_golden_50.jsonl"

#: The evaluator credential, under the name the Anthropic SDK itself reads.
ANTHROPIC_KEY_ENV: Final[str] = "ANTHROPIC_API_KEY"

#: The same credential under this project's own prefix - the name `.env.example` documents and
#: the name the GitHub Actions secret carries. Both are accepted because both are real: CI
#: exports the bare name into the step, while a developer's key lives in `.env` under the
#: prefixed one. Reading only the bare name (which is what this file used to do) meant the
#: placeholder the deliverable asked for was documented, committed, and ignored - so the
#: documented way to configure an evaluator produced a skipped test, which is the single most
#: likely reason this gate has never been run by anyone but CI.
PREFIXED_ANTHROPIC_KEY_ENV: Final[str] = "TAXCALC_AI_ANTHROPIC_API_KEY"

#: The gitignored local config. Read directly rather than through
#: :class:`~taxcalc_ai.settings.TaxcalcAiSettings`, because that model makes the LLM-proxy
#: fields mandatory: constructing it here would demand a proxy URL and key to answer a question
#: about the evaluator, and would fail on a machine that has the evaluator configured and
#: nothing else.
ENV_FILE: Final[Path] = Path(__file__).resolve().parents[1] / ".env"

#: Cheapest current Claude model that still judges reliably. The evaluator is called several
#: times per row per metric, so the model choice is most of this test's cost.
EVALUATOR_MODEL: Final[str] = "claude-haiku-4-5-20251001"

#: The floors recorded on W7 D2. Later days may raise these; nothing may lower them without a
#: recorded reason, which is the entire point of committing them.
FLOORS: Final[dict[str, float]] = {
    "faithfulness": 0.80,
    "answer_relevancy": 0.80,
    "context_precision": 0.65,
    "context_recall": 0.70,
}

#: RAGAS names its metrics with these keys in the result mapping.
MINIMUM_GOLDEN_ROWS: Final[int] = 50

#: Substrings that identify an Anthropic 400 as a provisioning problem rather than a bad request
#: this code built. A spend-capped workspace returns 400 `invalid_request_error`, NOT a 401, so
#: catching only AuthenticationError misses the most likely way a real deployment loses its
#: evaluator.
_SPEND_CAP_MARKERS: Final[tuple[str, ...]] = ("usage limit", "credit balance", "quota")

#: One message for both unavailable paths, so the CI report reads the same either way.
_UNAVAILABLE: Final[str] = (
    "evaluator unavailable ({detail}). This run evaluated NOTHING - the golden set and the "
    "floors are unchanged and untested. Raise the workspace spend limit in the Anthropic "
    "console (Settings -> Limits), then re-run to record a real baseline."
)


def _evaluator_api_key() -> str | None:
    """Find an evaluator credential, or ``None`` if this machine has none configured.

    Checks the process environment under both names first, then the gitignored ``.env`` under
    both. The ``.env`` lookup is what makes "paste your key into the file `.env.example` told
    you to copy" a working instruction instead of a near miss.

    The value is returned rather than exported here: a module-level import that quietly wrote a
    credential into ``os.environ`` would change the behaviour of every other test in the session.
    """
    for name in (ANTHROPIC_KEY_ENV, PREFIXED_ANTHROPIC_KEY_ENV):
        value = os.environ.get(name)
        if value:
            return value

    if not ENV_FILE.is_file():
        return None

    # python-dotenv arrives with pydantic-settings, which this project already depends on for
    # exactly this file's format - so the parsing rules match what TaxcalcAiSettings applies.
    from dotenv import dotenv_values

    values = dotenv_values(ENV_FILE)
    for name in (ANTHROPIC_KEY_ENV, PREFIXED_ANTHROPIC_KEY_ENV):
        value = values.get(name)
        # The committed example ships a `replace-me-...` placeholder. Treating that as a
        # credential would turn a clear skip into an authentication failure inside RAGAS's
        # executor, which surfaces as every metric returning NaN - a far worse error message
        # than the one below.
        if value and not value.startswith("replace-me"):
            return value
    return None


#: Resolved once at import so the skip decorator and the evaluator construction cannot disagree.
EVALUATOR_API_KEY: Final[str | None] = _evaluator_api_key()


def _provisioning_failure(exc: BaseException) -> BaseException | None:
    """Return the underlying Anthropic error if ``exc`` means "no usable evaluator", else None.

    The chain is walked because RAGAS runs metrics through its own executor and re-raises, so the
    Anthropic exception arrives wrapped rather than bare. Matching only on the outermost type
    would let a spend cap through as a hard failure.

    Deliberately narrow. An evaluator that cannot be reached is an infrastructure fact and is
    reported as a skip; anything else - a malformed dataset, a metric that errored, a model name
    that does not exist - is a real failure and is re-raised. A broad ``except Exception: skip``
    here would turn this gate into one that can never go red, which is worse than not having it.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            return current
        if isinstance(current, anthropic.BadRequestError):
            message = str(current).lower()
            if any(marker in message for marker in _SPEND_CAP_MARKERS):
                return current
        current = current.__cause__ or current.__context__
    return None


def _load_golden() -> Dataset:
    """Read the committed golden set into a HuggingFace :class:`~datasets.Dataset`.

    ``failure_mode`` is dropped before the dataset is built: it is this project's own
    bookkeeping about *why* a row exists, and RAGAS would carry an unrecognised column through
    the evaluation. The four columns that remain are the ones the metrics read.
    """
    rows = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line]
    if len(rows) < MINIMUM_GOLDEN_ROWS:
        pytest.fail(f"golden set has {len(rows)} rows; need >= {MINIMUM_GOLDEN_ROWS}")
    return Dataset.from_list(
        [{k: v for k, v in row.items() if k != "failure_mode"} for row in rows]
    )


#: Loggers RAGAS writes its per-job failures to. The executor catches each judging job's
#: exception itself, logs it, and writes NaN into that row - so this is the ONLY place the real
#: cause survives. Both names are watched because the package has moved the executor between
#: them across versions, and a capture that silently matched nothing would be worse than none.
_RAGAS_LOGGERS: Final[tuple[str, ...]] = ("ragas", "ragas.executor")


class _JudgeErrorCapture(logging.Handler):
    """Collect ERROR records emitted by RAGAS while an evaluation runs.

    RAGAS reports a dead evaluator as a complete result full of NaN rather than as an exception,
    which is why both gates have an all-NaN branch. That branch could say *that* nothing was
    judged but never *why* - and "why" is the whole difference between "raise the spend limit",
    "rotate the key", "the model id is wrong" and "CI has no egress". Those are four different
    fixes, and the run that could distinguish them was throwing the evidence away.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []
        # Records seen, by identity. The handler is attached to BOTH "ragas" and
        # "ragas.executor", and a child logger propagates the SAME LogRecord object to its
        # parent - so without this every message is captured twice and the job count doubles.
        # Caught by the test below, which logged 201 records and was told 402 failed. A wrong
        # count is worse than no count: "402 jobs failed" against a 50-row golden set invites
        # someone to go looking for a retry storm that never happened.
        self._seen: set[int] = set()

    def emit(self, record: logging.LogRecord) -> None:
        """Record one formatted ERROR message, ignoring a record already seen.

        ``format`` rather than ``record.getMessage()``: RAGAS attaches the underlying exception
        via ``exc_info``, and the exception type and message are the informative part - the log
        text itself is usually just "Exception raised in Job".
        """
        if id(record) in self._seen:
            return
        self._seen.add(id(record))
        self.messages.append(self.format(record))


@contextmanager
def _capture_judge_errors() -> Iterator[_JudgeErrorCapture]:
    """Attach the capture handler to RAGAS's loggers for the duration of the block.

    Handlers are removed and levels restored in a ``finally``, so a failing evaluation cannot
    leave a handler attached to a module-level logger for the rest of the session.
    """
    capture = _JudgeErrorCapture()
    capture.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    touched: list[tuple[logging.Logger, int, bool]] = []
    try:
        for name in _RAGAS_LOGGERS:
            logger = logging.getLogger(name)
            touched.append((logger, logger.level, logger.propagate))
            # RAGAS's own logger may be configured above ERROR by the time we get here; forcing
            # the level is what makes the capture independent of the library's defaults.
            logger.setLevel(logging.ERROR)
            logger.addHandler(capture)
        yield capture
    finally:
        for logger, level, propagate in touched:
            logger.removeHandler(capture)
            logger.setLevel(level)
            logger.propagate = propagate


def judge_failure_detail(capture: _JudgeErrorCapture) -> str:
    """Summarise captured judging errors for a skip message.

    Deduplicated and truncated: a fifty-row run against a dead evaluator produces ~200 identical
    records, and a skip reason that is two hundred copies of one line is not more informative
    than one copy - it is less, because nobody reads to the end of it.

    :param capture: The handler that was active during the evaluation.
    :returns: A human-readable detail string naming the distinct underlying errors.
    """
    if not capture.messages:
        return "every metric returned NaN and RAGAS logged nothing - cause unknown"
    seen: list[str] = []
    for message in capture.messages:
        collapsed = " ".join(message.split())
        if collapsed not in seen:
            seen.append(collapsed)
    shown = "; ".join(seen[:3])
    suffix = f" (+{len(seen) - 3} other distinct errors)" if len(seen) > 3 else ""
    return f"{len(capture.messages)} judging job(s) failed. Underlying: {shown}{suffix}"


def _run_eval() -> tuple[dict[str, float], _JudgeErrorCapture]:
    """Evaluate the golden set across the four core metrics and return the scores.

    Returns the capture handler alongside the scores so a caller that finds every metric NaN can
    report WHY rather than only THAT. RAGAS never raises in that case, so the log is the only
    surviving evidence - see :class:`_JudgeErrorCapture`.

    The evaluator LLM and the embedding model are both passed EXPLICITLY, which is a deliberate
    departure from the shape the lesson's reference snippet uses. Calling ``evaluate(dataset,
    metrics=[...])`` with nothing else lets RAGAS build its own defaults, and those defaults are
    OpenAI - so a CI job that supplies only ``ANTHROPIC_API_KEY`` does not evaluate against
    Claude, it fails with an OpenAI authentication error, or worse, silently bills a different
    provider if an ``OPENAI_API_KEY`` happens to be present in the environment. Naming the
    evaluator makes the judge a reviewable decision rather than a library default.

    The embeddings are the same local MiniLM the corpus was built with, so ``answer_relevancy``
    measures similarity in the same space the retrieval ranks in - and so the only thing this
    test sends over the network is the judging, not the text of every chunk.
    """
    from langchain_anthropic import ChatAnthropic

    # langchain_huggingface, not langchain_community: the community copy is deprecated and
    # emits a LangChainDeprecationWarning, which this project's filterwarnings policy turns into
    # an error. This one had an actual fix rather than needing an exemption - the class simply
    # moved packages.
    from langchain_huggingface import HuggingFaceEmbeddings
    from ragas.dataset_schema import EvaluationResult

    # Imported from the `.base` modules rather than from `ragas.embeddings` / `ragas.llms`.
    # Those two packages re-export these names through a `DeprecationHelper`, which warns on
    # ATTRIBUTE ACCESS as well as on construction; importing the real classes directly leaves
    # one deprecation site instead of two. Both are exempted by message in pyproject.toml.
    #
    # The legacy wrappers are kept deliberately, because the modern replacements do not fit the
    # `evaluate()` path this test uses - both checked against ragas 0.4.3, not assumed:
    #   * `llm_factory(...)` returns an `InstructorLLM`, which is NOT a `BaseRagasLLM`, and
    #     `evaluate()` accepts only `BaseRagasLLM | LangchainLLM`.
    #   * the modern `ragas.embeddings.HuggingFaceEmbeddings` is a `BaseRagasEmbedding`, whose
    #     interface is `embed_text`/`embed_texts`; `evaluate()` assigns whatever it is given
    #     straight onto the metric, and the old `AnswerRelevancy` calls `embed_query` and
    #     `embed_documents`. Passing the modern provider would raise AttributeError at judging
    #     time - a failure that only appears when a real credential is present.
    # Moving off them means rewriting this function against `ragas.metrics.collections`, whose
    # metrics `evaluate()` cannot drive at all. That is a real migration, not an import swap.
    from ragas.embeddings.base import LangchainEmbeddingsWrapper
    from ragas.llms.base import LangchainLLMWrapper

    # Exported rather than passed as a constructor argument: ChatAnthropic takes the key under an
    # alias (`api_key` for the field `anthropic_api_key`), and threading a credential through an
    # aliased pydantic field is the kind of detail that breaks silently on a minor upgrade. Set
    # here, inside the only function that needs it, and only when it is not already set -
    # `_evaluator_api_key` may have read it out of `.env`, where the SDK cannot see it.
    if EVALUATOR_API_KEY is not None:
        os.environ.setdefault(ANTHROPIC_KEY_ENV, EVALUATOR_API_KEY)

    evaluator = LangchainLLMWrapper(ChatAnthropic(model=EVALUATOR_MODEL, timeout=120))
    embeddings = LangchainEmbeddingsWrapper(HuggingFaceEmbeddings(model_name=MODEL_NAME))

    # RunConfig's defaults are max_retries=10 with backoff to 60s a wait. Against a healthy
    # evaluator that is sensible resilience; against a dead one - a spend-capped workspace, a
    # revoked key - it means every one of ~200 jobs exhausts ten retries before the run
    # finishes. Measured: 13m40s to report a failure that was knowable in the first few seconds,
    # inside a CI job with a 30 minute budget. Three retries keeps genuine transient handling
    # and bounds the dead-evaluator case to roughly a minute.
    with _capture_judge_errors() as capture:
        result = evaluate(
            _load_golden(),
            metrics=[Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()],
            llm=evaluator,
            embeddings=embeddings,
            run_config=RunConfig(max_retries=3, max_wait=8, timeout=60),
        )
    # evaluate() is typed as returning EvaluationResult | Executor - the second arm is the
    # deferred-execution path this call does not take. Asserting the type is what lets the
    # aggregation below be checked rather than silently operating on an Executor.
    assert isinstance(result, EvaluationResult), type(result)

    # Aggregate from the per-row frame rather than from the result's repr mapping: to_pandas()
    # is the documented, stable surface, and taking the mean here makes explicit what the
    # headline number actually is - an unweighted average over the fifty rows, which is why a
    # single badly-scored row moves a metric by ~0.02 and not more.
    frame = result.to_pandas()
    scores = {metric: float(frame[metric].mean()) for metric in FLOORS if metric in frame.columns}
    return scores, capture


def test_golden_set_is_committed_and_covers_the_named_failure_modes() -> None:
    """The golden set exists, is big enough, and is not uniformly clean.

    This runs without credentials and without a network, which is the point: it is the half of
    the baseline that can fail fast in every environment. If the golden set were accidentally
    regenerated as fifty clean rows, the thresholds below would rise to ~1.0 and stop detecting
    regressions - and nobody would notice, because the build would be greener than before.
    """
    rows = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line]

    assert len(rows) >= MINIMUM_GOLDEN_ROWS
    for row in rows:
        assert set(row) == {"question", "answer", "contexts", "ground_truth", "failure_mode"}
        assert isinstance(row["contexts"], list)
        assert all(isinstance(c, str) and c for c in row["contexts"])
        assert row["question"] and row["answer"] and row["ground_truth"]

    modes = {row["failure_mode"] for row in rows}
    assert {"missing_context", "junk_context", "near_duplicate_context"} <= modes, modes
    # Headroom check: a set that is mostly clean cannot detect a regression.
    clean = sum(1 for row in rows if row["failure_mode"] == "clean")
    assert clean < len(rows), "every row is clean; the floors would have no headroom to fall"


@pytest.mark.slow
@pytest.mark.skipif(
    EVALUATOR_API_KEY is None,
    reason=(
        f"no evaluator credential: set {ANTHROPIC_KEY_ENV} or {PREFIXED_ANTHROPIC_KEY_ENV} in "
        f"the environment, or put either in {ENV_FILE.name} (gitignored). The evaluation makes "
        "real judging calls; it is gated on the credential rather than xfailed so a missing "
        "secret in CI is visible as a skip in the report instead of passing as a green test. "
        "A skip here means the four floors are DECLARED, not measured."
    ),
)
def test_ragas_baseline_thresholds() -> None:
    """Every metric is at or above the floor recorded on W7 D2.

    The assertion message carries the whole score mapping, not just the failing metric: when
    this fails the next question is always "did one metric move or did all of them", and a
    message naming only the first failure cannot answer it.
    """
    try:
        scores, capture = _run_eval()
    # Broad here, narrowed on the very next line. Kept for the paths that DO propagate - an
    # unusable key can raise while the client is built, before any job is queued.
    except Exception as exc:
        underlying = _provisioning_failure(exc)
        if underlying is None:
            raise
        pytest.skip(_UNAVAILABLE.format(detail=f"{type(underlying).__name__}: {underlying}"))

    # The path that actually fires. RAGAS's executor catches each job's exception itself, logs
    # it at ERROR, and writes NaN into that row's score - so a spend-capped evaluator does not
    # raise anything at all, it returns a full result whose every value is NaN. Left unhandled
    # that reads as `assert nan >= 0.80` failing, which is indistinguishable in the CI log from
    # a genuine quality regression, and is the wrong thing to page someone about.
    #
    # ALL metrics NaN means no judgement happened anywhere: a provisioning fact, reported as a
    # skip. SOME metrics NaN is a different animal - the evaluator was reachable and something
    # about the data or a specific metric broke - so that still fails, loudly, below.
    if scores and all(math.isnan(value) for value in scores.values()):
        # The captured detail is the point: "nothing was judged" is the same annotation whether
        # the workspace is capped, the key is revoked, the model id is wrong or CI has no egress,
        # and those are four different fixes.
        pytest.skip(_UNAVAILABLE.format(detail=judge_failure_detail(capture)))

    for metric, floor in FLOORS.items():
        assert metric in scores, f"{metric} missing from RAGAS result: {scores}"
        # Explicit, because `nan >= floor` is False rather than an error: without this the
        # failure message would blame the floor for what is actually an un-evaluated metric.
        assert not math.isnan(scores[metric]), (
            f"{metric} is NaN - it was not evaluated, rather than scoring low: {scores}"
        )
        assert scores[metric] >= floor, f"{metric} below the W7 D2 floor {floor}: {scores}"
