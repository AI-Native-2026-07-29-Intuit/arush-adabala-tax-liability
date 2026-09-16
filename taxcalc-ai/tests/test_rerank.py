# taxcalc-ai/tests/test_rerank.py
"""MMR's two limiting cases, the reranker's lift, and the timeout-and-fallback path.

No database here: both stages operate on lists already in memory, so a container would only
slow the suite down. The cross-encoder itself IS real - the lift assertion is a claim about
what ``bge-reranker-base`` does with a query/passage pair, and a stubbed scorer would assert
what the test author believed instead.

The reranker is module-cached in production (``rerank._RERANKER``), and these tests rely on
that: the first test that reranks pays the ~1.1 GB model load and the rest do not.
"""

from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray
from sentence_transformers import SentenceTransformer

from taxcalc_ai import rerank as rerank_module
from taxcalc_ai.corpus import MODEL_NAME
from taxcalc_ai.rerank import (
    DEFAULT_RERANK_TOP_K,
    bge_rerank,
    mmr_pick,
)

#: Cosine-similarity ceiling the diversification assertion holds consecutive picks under.
#: 0.95 rather than something tighter: MMR diversifies, it does not guarantee orthogonality,
#: and near-duplicates in this fixture sit around 0.97-0.99.
NEAR_DUPLICATE_SIM = 0.95


@pytest.fixture(scope="module")
def embedder() -> SentenceTransformer:
    """The bi-encoder MMR measures candidate-to-candidate similarity with."""
    return SentenceTransformer(MODEL_NAME)


#: Four near-duplicates of one fact followed by four genuinely different facts. The shape
#: matters: this is exactly what RRF's top-60 looks like after an overlapping chunker, and it
#: is the input on which lambda=1.0 and lambda=0.0 visibly diverge.
CANDIDATE_TEXTS = [
    "The standard deduction for a single filer is $14,600 for tax year 2026.",
    "For tax year 2026 the single filer standard deduction is $14,600.",
    "A single filer's standard deduction in 2026 equals $14,600.",
    "Standard deduction, single, 2026: $14,600.",
    "Federal ordinary-income brackets are marginal, so only income inside a band is taxed "
    "at that band's rate.",
    "The alternative minimum tax exemption for a single filer is $85,700 before phase-out.",
    "Qualified dividends are taxed at the long-term capital gains rates rather than at "
    "ordinary rates.",
    "Estimated tax payments are due quarterly, and a shortfall accrues an underpayment penalty.",
]


def _candidates() -> list[tuple[str, str, float]]:
    """The fixture as ``(chunk_id, chunk_text, score)`` triples in a plausible fused order."""
    return [(f"chunk-fixture-p{i}", text, 1.0 / (61 + i)) for i, text in enumerate(CANDIDATE_TEXTS)]


def _embed(embedder: SentenceTransformer, texts: list[str]) -> NDArray[np.float32]:
    """Unit-normalised ``float32`` embeddings, matching what the corpus was written with."""
    vectors: NDArray[np.float32] = embedder.encode(
        texts, normalize_embeddings=True, convert_to_numpy=True
    ).astype(np.float32)
    return vectors


def test_mmr_at_lambda_one_reproduces_plain_cosine_top_k(embedder: SentenceTransformer) -> None:
    """``lambda_param=1.0`` removes the redundancy penalty, so MMR degenerates to cosine top-k.

    Worth pinning as its own case because it is the sanity check on the objective: if the
    diversification term is wired with the wrong sign, or the penalty is applied even at
    lambda 1.0, this test fails while a mid-lambda test might still look plausible.
    """
    candidates = _candidates()
    query_vec = _embed(embedder, ["what is the standard deduction for a single filer"])[0]
    cand_vecs = _embed(embedder, CANDIDATE_TEXTS)

    picked = mmr_pick(query_vec, candidates, embedder, k=4, lambda_param=1.0)

    # The reference order: plain cosine similarity, descending.
    similarity = cand_vecs @ query_vec
    expected = [candidates[i][0] for i in np.argsort(-similarity)[:4]]

    assert [chunk_id for chunk_id, _, _ in picked] == expected

    # The incoming RRF score is carried through untouched rather than replaced by the MMR
    # objective, which has no meaning outside this candidate set.
    by_id = {chunk_id: score for chunk_id, _, score in candidates}
    assert all(score == by_id[chunk_id] for chunk_id, _, score in picked)


def test_mmr_at_lambda_zero_spreads_its_picks(embedder: SentenceTransformer) -> None:
    """``lambda_param=0.0`` ignores the query entirely and no two consecutive picks are twins.

    The four near-duplicates at the head of the fixture are what makes this meaningful: a
    cosine top-k over the same input returns several of them, and this must not.
    """
    candidates = _candidates()
    query_vec = _embed(embedder, ["what is the standard deduction for a single filer"])[0]

    picked = mmr_pick(query_vec, candidates, embedder, k=4, lambda_param=0.0)
    picked_vecs = _embed(embedder, [text for _, text, _ in picked])

    consecutive = [float(picked_vecs[i] @ picked_vecs[i + 1]) for i in range(len(picked_vecs) - 1)]
    assert all(sim <= NEAR_DUPLICATE_SIM for sim in consecutive), consecutive

    # And the contrast that makes the point: undiversified selection DOES return the twins.
    greedy = mmr_pick(query_vec, candidates, embedder, k=4, lambda_param=1.0)
    near_duplicate_ids = {f"chunk-fixture-p{i}" for i in range(4)}
    assert len({c[0] for c in greedy} & near_duplicate_ids) > len(
        {c[0] for c in picked} & near_duplicate_ids
    )

    # An empty candidate list is a legitimate outcome of a query that matched nothing, and must
    # not raise inside the retrieval path.
    assert mmr_pick(query_vec, [], embedder) == []


def test_bge_rerank_lifts_the_gold_chunk_out_of_the_retrieval_tail() -> None:
    """A chunk buried at rank 5 by retrieval order reaches the top-2 after cross-encoding.

    This is what the cross-encoder buys and what a bi-encoder cannot: it reads the query and
    the passage in the same forward pass, so it can score "this passage answers *that*
    question" rather than "these two texts are about similar things".

    Asserted as top-2 rather than exactly rank 1. Rank 1 is what happens on this fixture today,
    but pinning it would make a reranker version bump fail a test about *lift*; top-2 out of
    eight is a claim about the mechanism working, not about the model's exact weights.

    ``timeout_ms`` is deliberately enormous, and that is the second thing this test learned the
    hard way. It first ran with the production 300 ms budget and passed locally, then failed on
    a GitHub runner with ``timed_out=True`` - eight ``(query, passage)`` pairs through
    ``bge-reranker-base`` on a shared CPU runner simply take longer than 300 ms. Asserting the
    budget here made a test about RANKING depend on the CPU the suite happens to run on, which
    is a flake, not a finding. The timeout has its own dedicated test below, where the breach is
    forced with ``timeout_ms=1`` and is therefore deterministic on any hardware.
    """
    question = "What is the alternative minimum tax exemption for a single filer?"
    gold_id = "chunk-fixture-p5"  # the AMT sentence, 6th in retrieval order
    candidates = _candidates()
    assert candidates[5][0] == gold_id

    # 60s: high enough that the fallback cannot fire on any runner, so what fails here is the
    # ranking and only the ranking.
    results, timed_out = bge_rerank(
        question, candidates, top_k=DEFAULT_RERANK_TOP_K, timeout_ms=60_000
    )

    assert timed_out is False
    assert len(results) == DEFAULT_RERANK_TOP_K
    assert gold_id in [chunk_id for chunk_id, _, _ in results[:2]], results
    # Cross-encoder scores replace the incoming RRF scores: the two are on different scales, and
    # a mixture would be indistinguishable from either.
    assert [score for _, _, score in results] == sorted(
        (score for _, _, score in results), reverse=True
    )
    assert not any(score == pytest.approx(1.0 / 61) for _, _, score in results)


def test_the_timeout_falls_back_to_retrieval_order_and_reports_the_breach() -> None:
    """``timeout_ms=1`` fires the fallback: retrieval order, incoming scores, flag set True.

    The flag is the whole contract. A soft failure that is not reported is just a silent quality
    regression, so this asserts BOTH halves - that the request still returns usable context, and
    that the breach is visible to the caller.
    """
    candidates = _candidates()

    results, timed_out = bge_rerank("any question at all", candidates, top_k=3, timeout_ms=1)

    assert timed_out is True
    # Retrieval order, not reranked order, and the incoming scores are preserved untouched
    # because no rerank score exists on this path.
    assert results == candidates[:3]

    # The soft failure is a fallback, not an exception: a slow reranker must not become a failed
    # request. Asserting no raise is the point of calling it at all here.
    empty, empty_flag = bge_rerank("q", [], timeout_ms=1)
    assert empty == [] and empty_flag is False


def test_the_reranker_model_is_constructed_exactly_once_per_process() -> None:
    """``_get_reranker`` is module-cached, so the ~1.1 GB load happens once.

    Not a micro-optimisation: constructing the model per call takes seconds, which makes the
    300 ms budget unmeetable by construction and attributes the load to whichever request was
    unlucky enough to be first.
    """
    first = rerank_module._get_reranker()
    second = rerank_module._get_reranker()

    assert first is second
    assert rerank_module._RERANKER is first
