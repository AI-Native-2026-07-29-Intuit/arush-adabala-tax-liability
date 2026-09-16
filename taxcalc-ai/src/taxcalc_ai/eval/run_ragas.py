# taxcalc-ai/src/taxcalc_ai/eval/run_ragas.py
"""Generate the before-vs-after RAGAS report across the four W7 D3 upgrade flags.

``uv run python -m taxcalc_ai.eval.run_ragas --all-on`` (or ``--matrix`` for every column)
answers the question the four upgrades exist to answer: which of them actually moved the
metrics, and by how much.

**Why a matrix rather than a single before/after pair.** Turning on hybrid retrieval, MMR,
reranking and metadata filtering together and observing that faithfulness rose tells you the
bundle helped. It does not tell you that all four helped - one of them may be neutral, and one
may be *negative* and masked by the others. Six columns (baseline, each flag alone, all four)
is what makes that attributable, and attribution is what lets a later day remove the stage that
is paying latency for nothing.

**The contexts scored are the pipeline's OWN retrieved chunks, not the golden set's.** This is
the substantive difference from W7 D2's ``test_ragas_thresholds.py``, which evaluates a
committed golden file whose ``contexts`` and ``answer`` are fixed - a stable baseline for
detecting *evaluator* drift, but blind to a retrieval change by construction. Here the question
and ``ground_truth`` come from the golden set and everything else is produced live, which is
the only shape in which changing the retriever can move ``context_precision`` at all.

**Nothing in here is a threshold.** The gate is ``tests/test_ragas_gate.py``; this writes a
markdown table. Keeping measurement and gating apart means a report can be regenerated for
information without a red build, and a gate can fail without anyone having to interpret a table.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.eval.run_ragas")

#: The committed golden set. Only ``question`` and ``ground_truth`` are read; ``answer`` and
#: ``contexts`` are produced by the pipeline under test.
GOLDEN_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3] / "tests" / "golden" / "taxcalc_golden_50.jsonl"
)

#: Where the report is written.
DEFAULT_REPORT_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3] / "docs" / "ragas" / "w7d3.md"
)

#: Environment variable carrying the Redis URL for the semantic cache.
REDIS_URL_ENV: Final[str] = "TAXCALC_AI_REDIS_URL"

#: The metrics reported, in column order.
METRICS: Final[tuple[str, ...]] = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)


@dataclass(frozen=True, slots=True)
class Configuration:
    """One column of the report: a named combination of the four pipeline flags.

    :param name: Column heading in the report.
    :param use_hybrid: Run the sparse retriever and fuse by RRF.
    :param use_mmr: Diversify the fused candidates.
    :param use_rerank: Cross-encode the diversified candidates.
    :param use_filter: Apply the JSONB metadata pre-filter.
    """

    name: str
    use_hybrid: bool
    use_mmr: bool
    use_rerank: bool
    use_filter: bool


#: The six columns. ``baseline`` is every flag off, which reproduces the W7 D2 single-cosine
#: behaviour - that is what makes the deltas comparable to yesterday's recorded numbers rather
#: than to a differently-shaped pipeline.
CONFIGURATIONS: Final[tuple[Configuration, ...]] = (
    Configuration("W7D2 baseline", False, False, False, False),
    Configuration("hybrid", True, False, False, False),
    Configuration("rerank", False, False, True, False),
    Configuration("mmr", False, True, False, False),
    Configuration("filter", False, False, False, True),
    Configuration("all-on", True, True, True, True),
)


def _load_questions(limit: int | None = None) -> list[tuple[str, str]]:
    """Read ``(question, ground_truth)`` pairs from the committed golden set.

    :param limit: Evaluate only the first ``limit`` rows. Judging cost is linear in rows times
        metrics times configurations, so a six-column matrix over fifty rows is 1,200 metric
        evaluations - this is the knob for a smoke run.
    :returns: The pairs, in file order.
    :raises FileNotFoundError: if the golden set is missing, which means the working tree is
        incomplete rather than that the evaluation found nothing.
    """
    rows = [json.loads(line) for line in GOLDEN_PATH.read_text().splitlines() if line.strip()]
    pairs = [(str(row["question"]), str(row["ground_truth"])) for row in rows]
    return pairs[:limit] if limit is not None else pairs


def run_configuration(
    configuration: Configuration,
    questions: Sequence[tuple[str, str]],
    tenant_id: str = "tenant-a",
) -> dict[str, float]:
    """Run the pipeline under one flag combination and score the result with RAGAS.

    Imports are function-local because this module is also imported to read
    :data:`CONFIGURATIONS` (by the report renderer and by tests), and importing
    ``sentence_transformers`` plus ``ragas`` to read a tuple of dataclasses is several seconds
    and ~80 MB for nothing.

    The semantic cache is bypassed by giving every configuration its own tenant suffix. A shared
    tenant would make column two answer from column one's cache - the flags would appear to have
    no effect, which is a result indistinguishable from "the upgrades did nothing".

    :param configuration: The flag combination to measure.
    :param questions: ``(question, ground_truth)`` pairs.
    :param tenant_id: Base tenant whose corpus to search.
    :returns: Mean score per metric.
    """
    import psycopg
    import redis
    from anthropic import Anthropic
    from datasets import Dataset

    from taxcalc_ai.pgvector_loader import dsn_from_env
    from taxcalc_ai.rag import retrieve_and_generate

    client = Anthropic()
    cache = redis.from_url(os.environ[REDIS_URL_ENV])

    records: list[dict[str, object]] = []
    with psycopg.connect(dsn_from_env()) as conn:
        for question, ground_truth in questions:
            answer = retrieve_and_generate(
                question,
                tenant_id,
                anthropic=client,
                conn=conn,
                r=cache,
                use_hybrid=configuration.use_hybrid,
                use_mmr=configuration.use_mmr,
                use_rerank=configuration.use_rerank,
                use_filter=configuration.use_filter,
            )
            # isinstance-guarded rather than indexed: retrieve_and_generate returns
            # dict[str, object] - the payload is heterogeneous by design (a string, a list, a
            # bool, a mapping) - so the shape has to be narrowed before it can be walked.
            citations = answer.get("citations")
            contexts = (
                [
                    str(citation["chunk_text"])
                    for citation in citations
                    if isinstance(citation, dict)
                ]
                if isinstance(citations, list)
                else []
            )
            records.append(
                {
                    "question": question,
                    "answer": str(answer.get("text", "")),
                    # RAGAS requires a non-empty context list: context_precision over zero
                    # retrieved chunks is undefined and comes back NaN, which would be reported
                    # as an un-evaluated metric rather than as "retrieval found nothing". The
                    # placeholder makes a no-retrieval query score badly, which is the truth.
                    "contexts": contexts or ["<no context retrieved>"],
                    "ground_truth": ground_truth,
                }
            )

    return _score(Dataset.from_list(records))


def _score(dataset: object) -> dict[str, float]:
    """Score a live dataset with the same evaluator the W7 D2 gate uses.

    The evaluator LLM and the embeddings are passed explicitly for the reason
    ``tests/test_ragas_thresholds.py`` documents at length: ``evaluate(dataset, metrics=[...])``
    with nothing else lets RAGAS build its own defaults, and those defaults are OpenAI - so a
    job supplying only an Anthropic key does not evaluate against Claude, it fails on an OpenAI
    authentication error, or silently bills a different provider if an ``OPENAI_API_KEY``
    happens to be in the environment.

    :param dataset: A HuggingFace ``Dataset`` of live pipeline output.
    :returns: Mean score per metric in :data:`METRICS` that the result actually carries.
    """
    from langchain_anthropic import ChatAnthropic
    from langchain_huggingface import HuggingFaceEmbeddings
    from ragas import evaluate
    from ragas.dataset_schema import EvaluationResult
    from ragas.embeddings.base import LangchainEmbeddingsWrapper
    from ragas.llms.base import LangchainLLMWrapper
    from ragas.metrics._answer_relevance import AnswerRelevancy
    from ragas.metrics._context_precision import ContextPrecision
    from ragas.metrics._context_recall import ContextRecall
    from ragas.metrics._faithfulness import Faithfulness
    from ragas.run_config import RunConfig

    from taxcalc_ai.corpus import MODEL_NAME

    # claude-haiku-4-5 as the judge: it is judging answers, not producing them, and the
    # evaluator is called several times per row per metric, so the model choice is most of the
    # cost of this script.
    evaluator = LangchainLLMWrapper(ChatAnthropic(model="claude-haiku-4-5", timeout=120))
    embeddings = LangchainEmbeddingsWrapper(HuggingFaceEmbeddings(model_name=MODEL_NAME))

    result = evaluate(
        dataset,
        metrics=[Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()],
        llm=evaluator,
        embeddings=embeddings,
        # max_retries=3, not the default 10: against a dead evaluator the defaults mean every
        # job exhausts ten retries with backoff to 60s before the run reports anything, which
        # measured at 13m40s for a failure knowable in the first few seconds.
        run_config=RunConfig(max_retries=3, max_wait=8, timeout=60),
    )
    assert isinstance(result, EvaluationResult), type(result)
    frame = result.to_pandas()
    return {metric: float(frame[metric].mean()) for metric in METRICS if metric in frame.columns}


def render_report(scores: Mapping[str, Mapping[str, float]]) -> str:
    """Render the before-vs-after markdown table, with a delta block against the baseline.

    :param scores: Configuration name -> metric -> mean score. Configurations absent from the
        mapping are rendered as ``n/m`` (not measured) rather than omitted or zeroed: a column
        that was not run must not read as a column that scored badly, and must not silently
        disappear from a report someone is using to decide which stage to keep.
    :returns: The markdown body, without a title.
    """
    names = [c.name for c in CONFIGURATIONS]
    baseline_name = CONFIGURATIONS[0].name

    def cell(name: str, metric: str) -> str:
        value = scores.get(name, {}).get(metric)
        return f"{value:.2f}" if value is not None else "n/m"

    lines = ["| Metric | " + " | ".join(names) + " |"]
    lines.append("|" + "---|" * (len(names) + 1))
    for metric in METRICS:
        lines.append(f"| {metric} | " + " | ".join(cell(name, metric) for name in names) + " |")

    upgrades = names[1:]
    lines += ["", "| Delta (vs baseline) | " + " | ".join(upgrades) + " |"]
    lines.append("|" + "---|" * (len(upgrades) + 1))
    for metric in METRICS:
        deltas: list[str] = []
        for name in upgrades:
            base = scores.get(baseline_name, {}).get(metric)
            value = scores.get(name, {}).get(metric)
            deltas.append(
                f"{value - base:+.2f}" if base is not None and value is not None else "n/m"
            )
        lines.append(f"| {metric} | " + " | ".join(deltas) + " |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    :param argv: Argument vector, or ``None`` to read ``sys.argv``.
    :returns: Process exit status. 0 on a completed run.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all-on",
        action="store_true",
        help="measure the baseline and the all-four-on configuration only (2 columns)",
    )
    group.add_argument(
        "--matrix",
        action="store_true",
        help="measure every configuration, including each flag in isolation (6 columns)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="evaluate only the first N golden rows; judging cost is linear in rows",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args(argv)

    selected = CONFIGURATIONS if args.matrix else (CONFIGURATIONS[0], CONFIGURATIONS[-1])
    questions = _load_questions(args.limit)

    scores: dict[str, dict[str, float]] = {}
    for configuration in selected:
        _LOG.info(
            "eval.configuration.started",
            extra={"event": "eval.configuration.started", "configuration": configuration.name},
        )
        scores[configuration.name] = run_configuration(configuration, questions)

    body = render_report(scores)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        f"# Before-vs-After RAGAS Report - W7 D3\n\n"
        f"Rows evaluated: {len(questions)} of the committed golden set.\n\n{body}\n"
    )
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
