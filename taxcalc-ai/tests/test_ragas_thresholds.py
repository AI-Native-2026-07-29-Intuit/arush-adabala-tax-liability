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
import os
from pathlib import Path
from typing import Final

import pytest
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)

from taxcalc_ai.corpus import MODEL_NAME

GOLDEN: Final[Path] = Path(__file__).resolve().parent / "golden" / "taxcalc_golden_50.jsonl"

#: The evaluator credential. RAGAS judges answers rather than producing them, so a smaller and
#: cheaper model than production is the right choice here.
ANTHROPIC_KEY_ENV: Final[str] = "ANTHROPIC_API_KEY"

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


def _run_eval() -> dict[str, float]:
    """Evaluate the golden set across the four core metrics and return the scores.

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
    from langchain_community.embeddings import HuggingFaceEmbeddings
    from ragas.dataset_schema import EvaluationResult
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    evaluator = LangchainLLMWrapper(ChatAnthropic(model=EVALUATOR_MODEL, timeout=120))
    embeddings = LangchainEmbeddingsWrapper(HuggingFaceEmbeddings(model_name=MODEL_NAME))

    result = evaluate(
        _load_golden(),
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=evaluator,
        embeddings=embeddings,
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
    return {metric: float(frame[metric].mean()) for metric in FLOORS if metric in frame.columns}


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
    ANTHROPIC_KEY_ENV not in os.environ,
    reason=(
        f"{ANTHROPIC_KEY_ENV} is not set. The evaluation makes real judging calls; it is gated "
        "on the credential rather than xfailed so a missing secret in CI is visible as a skip "
        "in the report instead of passing as a green test."
    ),
)
def test_ragas_baseline_thresholds() -> None:
    """Every metric is at or above the floor recorded on W7 D2.

    The assertion message carries the whole score mapping, not just the failing metric: when
    this fails the next question is always "did one metric move or did all of them", and a
    message naming only the first failure cannot answer it.
    """
    scores = _run_eval()

    for metric, floor in FLOORS.items():
        assert metric in scores, f"{metric} missing from RAGAS result: {scores}"
        assert scores[metric] >= floor, f"{metric} below the W7 D2 floor {floor}: {scores}"
