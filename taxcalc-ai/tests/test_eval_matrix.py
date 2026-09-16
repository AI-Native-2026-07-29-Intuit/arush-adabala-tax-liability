# taxcalc-ai/tests/test_eval_matrix.py
"""The before-vs-after report harness: configurations, rendering, and the unmeasured cell.

No evaluator and no database here. What this file pins is the *reporting* contract, and the
most important part of it is what happens to a configuration that was not run: it must render
as ``n/m``, not as a zero and not as an omitted column. A column that silently disappears from
this report, or reads as a bad score when it simply was not measured, makes the document
actively misleading - and the document's only purpose is to be the input to "is the reranker
worth its latency".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from taxcalc_ai.eval.run_ragas import (
    CONFIGURATIONS,
    METRICS,
    Configuration,
    _load_questions,
    main,
    render_report,
)


def test_the_configuration_matrix_isolates_each_flag_and_ends_with_all_on() -> None:
    """Six columns: a baseline with every flag off, each flag alone, then all four together.

    The baseline column must have every flag off, because that is what makes its numbers
    comparable to the W7 D2 recorded floors rather than to a differently-shaped pipeline. The
    single-flag columns are what make a delta attributable to a stage - without them, a neutral
    stage and a stage masked by the others are indistinguishable.
    """
    assert len(CONFIGURATIONS) == 6

    baseline = CONFIGURATIONS[0]
    assert not any(
        (baseline.use_hybrid, baseline.use_mmr, baseline.use_rerank, baseline.use_filter)
    )

    all_on = CONFIGURATIONS[-1]
    assert all((all_on.use_hybrid, all_on.use_mmr, all_on.use_rerank, all_on.use_filter))

    # Each of the four middle columns turns on exactly one flag, so its delta is attributable.
    for configuration in CONFIGURATIONS[1:-1]:
        flags = (
            configuration.use_hybrid,
            configuration.use_mmr,
            configuration.use_rerank,
            configuration.use_filter,
        )
        assert sum(flags) == 1, configuration


def test_an_unmeasured_configuration_renders_as_not_measured() -> None:
    """A missing configuration is ``n/m`` in both tables - never 0.00, never absent.

    The delta row is the subtler half: a measured column against an unmeasured baseline has no
    meaningful delta, and rendering one (``+0.89``) would be a fabricated number in the row a
    reader trusts most.
    """
    report = render_report({})

    for metric in METRICS:
        assert f"| {metric} |" in report
    assert "n/m" in report
    # Every column still appears, so nothing vanishes from the report by having not been run.
    for configuration in CONFIGURATIONS:
        assert configuration.name in report
    assert "0.00" not in report

    # A measured baseline with one measured column produces a real delta; the unmeasured
    # columns beside it stay n/m rather than borrowing the baseline's number.
    partial = render_report(
        {
            CONFIGURATIONS[0].name: dict.fromkeys(METRICS, 0.80),
            CONFIGURATIONS[1].name: dict.fromkeys(METRICS, 0.85),
        }
    )
    assert "+0.05" in partial
    assert "0.80" in partial and "0.85" in partial
    assert "n/m" in partial


def test_the_golden_questions_are_read_and_the_limit_is_honoured() -> None:
    """Only ``question`` and ``ground_truth`` are taken from the golden set.

    The committed ``answer`` and ``contexts`` are deliberately ignored: they are fixed, which
    makes the W7 D2 threshold test blind to a retrieval change by construction. Here the answer
    and the contexts are produced live, which is the only shape in which changing the retriever
    can move ``context_precision`` at all.
    """
    questions = _load_questions()

    assert len(questions) >= 50
    assert all(isinstance(q, str) and q for q, _ in questions)
    assert all(isinstance(g, str) and g for _, g in questions)

    # The limit is the cost knob: judging is linear in rows x metrics x configurations, so a
    # six-column matrix over fifty rows is 1,200 metric evaluations.
    assert _load_questions(limit=5) == questions[:5]


def test_main_writes_the_report_and_refuses_an_ambiguous_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``main`` writes the markdown file, and requires exactly one of ``--all-on``/``--matrix``.

    ``run_configuration`` is stubbed: this asserts the CLI wiring and the file write, which are
    the parts that would otherwise only ever be exercised by a run that costs money. The
    stub returns a fixed score mapping, so the numbers in the assertion are the stub's, not a
    measurement - which is the same distinction the report itself is careful about.
    """
    from taxcalc_ai.eval import run_ragas

    def _stub(
        configuration: Configuration,
        questions: object,
        tenant_id: str = "tenant-a",
    ) -> dict[str, float]:
        return dict.fromkeys(METRICS, 0.90 if configuration.use_rerank else 0.80)

    monkeypatch.setattr(run_ragas, "run_configuration", _stub)
    out = tmp_path / "w7d3.md"

    assert main(["--all-on", "--limit", "2", "--out", str(out)]) == 0

    body = out.read_text()
    assert body.startswith("# Before-vs-After RAGAS Report - W7 D3")
    assert "Rows evaluated: 2" in body
    assert "0.80" in body and "0.90" in body
    assert "+0.10" in body

    # Mutually exclusive AND required: neither flag, or both, is a usage error rather than a
    # default - "which configurations did that run measure" must never be implicit.
    with pytest.raises(SystemExit):
        main(["--out", str(out)])
    with pytest.raises(SystemExit):
        main(["--all-on", "--matrix", "--out", str(out)])
