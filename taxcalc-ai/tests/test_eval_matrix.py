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
    GATE_THRESHOLD,
    METRICS,
    SUB_GATE_FLAG,
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


def test_a_nan_score_renders_as_not_measured_rather_than_as_nan() -> None:
    """The spend-capped evaluator returns all-NaN without raising; NaN is not a measurement.

    This is the failure this project has actually hit, twice. RAGAS's executor catches each
    judging job's exception itself, logs it at ERROR and writes NaN into that row, so a capped
    or revoked evaluator returns a *complete* result whose every value is NaN. ``value is not
    None`` is ``True`` for NaN, so the naive renderer formats the string ``nan`` into every
    cell - and a report full of ``nan`` reads as a measurement that went badly rather than as
    one that never happened.
    """
    nan = float("nan")
    report = render_report({c.name: dict.fromkeys(METRICS, nan) for c in CONFIGURATIONS})

    assert "nan" not in report
    assert "n/m" in report
    # And the roll-up says the cells are not determinable, not that they are all below the gate:
    # an unmeasured cell is not a failing cell.
    assert "not determinable" in report


def test_measured_cells_below_the_gate_are_flagged_and_rolled_up() -> None:
    """Every measured cell below the gate is flagged inline and listed, faithfulness distinctly.

    The deliverable requires each sub-gate cell to be flagged. Doing that in the renderer is
    what makes it survive a regeneration - a hand-annotated table loses its annotations the
    first time anyone re-runs the harness.

    **The fixture values are derived from :data:`GATE_THRESHOLD`, not written as literals.** They
    used to be 0.72 and 0.91, chosen when the gate was 0.85 - and the 2026-09-18 re-baseline to
    0.70 made 0.72 a *passing* score, so this test failed while the behaviour it checks was
    perfectly intact. The threshold is explicitly allowed to move; a test of the flagging
    mechanism must not encode a particular value of it.
    """
    below = round(GATE_THRESHOLD - 0.10, 2)
    above = round(GATE_THRESHOLD + 0.10, 2)
    scores = {
        CONFIGURATIONS[0].name: dict.fromkeys(METRICS, below),
        CONFIGURATIONS[-1].name: dict.fromkeys(METRICS, above),
    }
    report = render_report(scores)

    assert f"{below:.2f}{SUB_GATE_FLAG}" in report
    # At or above the gate is not flagged; the gate value itself passes, so the boundary is not
    # off by one.
    assert f"{above:.2f}{SUB_GATE_FLAG}" not in report
    assert f"{GATE_THRESHOLD:.2f}{SUB_GATE_FLAG}" not in render_report(
        {CONFIGURATIONS[0].name: dict.fromkeys(METRICS, GATE_THRESHOLD)}
    )

    # faithfulness is called out as the one that fails a build; the other three are diagnostics.
    assert "`faithfulness`" in report and "**(gates the build)**" in report
    assert "`context_recall`" in report
    assert "**(gates the build)**" not in report.split("`context_recall`")[1]


def test_the_report_gate_matches_the_ci_gate() -> None:
    """The renderer's threshold and ``test_ragas_gate.py``'s gate are the same number.

    The threshold is duplicated as a literal so the report can be rendered without pytest
    installed. This is the assertion that stops the copy drifting - a report flagging cells
    against 0.80 while the build fails at 0.85 would be worse than no flagging at all.

    It earned its keep on 2026-09-18: the gate was re-baselined 0.85 -> 0.70 and this copy was
    not, and this test is what caught the pair diverging.
    """
    from .test_ragas_gate import FAITHFULNESS_GATE

    assert GATE_THRESHOLD == FAITHFULNESS_GATE


def test_a_run_that_measured_nothing_refuses_to_overwrite_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An all-NaN run exits non-zero and leaves the committed report byte-for-byte unchanged.

    Without this, the spend-capped case is a silent downgrade: the run "succeeds", writes a
    table of ``n/m`` cells over a document whose prose explains at length why it holds no
    numbers, and the explanation is gone. The report is the artefact a later day consults to
    decide whether the reranker earns its latency; losing the reason it is empty is losing the
    only useful thing it currently says.
    """
    from taxcalc_ai.eval import run_ragas

    monkeypatch.setattr(
        run_ragas,
        "run_configuration",
        lambda configuration, questions, tenant_id="tenant-a": dict.fromkeys(METRICS, float("nan")),
    )
    out = tmp_path / "w7d3.md"
    original = "# Committed report\n\nExplains at length why it holds no numbers.\n"
    out.write_text(original)

    assert main(["--matrix", "--limit", "1", "--out", str(out)]) == 1
    assert out.read_text() == original


def test_a_measured_run_splices_the_table_and_preserves_the_surrounding_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regeneration replaces only the marked region, keeping the deliverable's prose sections.

    Two of the three things the deliverable asks this report for - the attribution naming which
    upgrade moved which metric, and the sub-0.85 commentary - are prose. The first version of
    ``main`` wrote a title and a table over the whole file, so the first successful measurement
    run would have deleted both and taken the deliverable with them.
    """
    from taxcalc_ai.eval import run_ragas

    monkeypatch.setattr(
        run_ragas,
        "run_configuration",
        lambda configuration, questions, tenant_id="tenant-a": dict.fromkeys(
            METRICS, 0.90 if configuration.use_rerank else 0.80
        ),
    )
    out = tmp_path / "w7d3.md"
    out.write_text(
        f"# Title\n\nPreamble prose.\n\n{run_ragas.MATRIX_BEGIN}\n"
        f"| stale | table |\n{run_ragas.MATRIX_END}\n\n"
        "## What each column attributes\n\nrerank moves context_precision.\n"
    )

    assert main(["--matrix", "--limit", "1", "--out", str(out)]) == 0

    body = out.read_text()
    assert "Preamble prose." in body
    assert "## What each column attributes" in body
    assert "rerank moves context_precision." in body
    assert "| stale | table |" not in body
    assert "0.90" in body and "+0.10" in body
    # Idempotent: a second run must not nest or duplicate the generated region.
    assert main(["--matrix", "--limit", "1", "--out", str(out)]) == 0
    assert out.read_text().count(run_ragas.MATRIX_BEGIN) == 1
    assert out.read_text().count("## What each column attributes") == 1


def test_each_configuration_bumps_the_cache_epoch_so_columns_cannot_share_answers() -> None:
    """``run_configuration`` invalidates the cache per column, and does NOT fake the tenant.

    The bug this pins is silent and total. The pipeline checks the semantic cache first and
    returns on a hit, and the cache key is the quantised query vector - which does not vary
    with the four flags. So without a per-column invalidation, column two is served column
    one's answers verbatim: every column scores identically, every delta reads ``+0.00``, and
    the report concludes the four upgrades did nothing. Nothing errors.

    The second assertion matters as much as the first: the fix must not be a per-column tenant
    suffix. The tenant is a retrieval pre-filter as well as a cache-key component, and the
    corpus is seeded for ``tenant-a`` only - so suffixing it would leave every column but the
    baseline searching a tenant with no chunks, scoring badly for want of any context at all.
    """
    import inspect

    from taxcalc_ai.eval import run_ragas

    source = inspect.getsource(run_ragas.run_configuration)

    assert "bump_epoch(cache, tenant_id)" in source
    # The tenant reaches the pipeline unmodified - no f-string suffix, no concatenation.
    assert "retrieve_and_generate(\n                question,\n                tenant_id," in source
