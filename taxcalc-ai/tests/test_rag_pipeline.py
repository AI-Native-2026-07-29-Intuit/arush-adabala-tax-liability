# taxcalc-ai/tests/test_rag_pipeline.py
"""``retrieve_and_generate`` end to end: the flag matrix, the cache, and the citation shape.

Real Postgres and real Redis; a stubbed Anthropic client. The split is deliberate. The
retrieval, the fusion, the cache key and the tenant filter are all properties of those two
datastores and of this code, and a mock would agree with whatever the code assumed. Generation
is not: the model's prose is not under test, and calling it would make the suite cost money and
depend on a credential to assert something about plumbing.

This is the function W7 D4's MCP server publishes as a tool and W7 D5's LangGraph nodes call,
so its signature and return shape are a contract with two days that do not exist yet. That is
what most of these assertions are about.
"""

from __future__ import annotations

from typing import Final

import numpy as np
import psycopg
import pytest
import redis
from numpy.typing import NDArray
from sentence_transformers import SentenceTransformer

from taxcalc_ai.corpus import MODEL_NAME, CorpusRow, content_hash
from taxcalc_ai.pgvector_loader import load_rows
from taxcalc_ai.rag import (
    RAG_USE_HYBRID_ENV,
    RAG_USE_MMR_ENV,
    flag_from_env,
    retrieve_and_generate,
)

#: The tenant this module owns.
TENANT: Final[str] = "tenant-pipeline"

#: The generated text the stub returns, so a cache hit is distinguishable from a fresh run.
STUB_ANSWER: Final[str] = "The single standard deduction is $14,600."

#: Corpus rows seeded for this module. Heterogeneous so MMR has redundancy to remove and the
#: metadata filter has something to exclude.
SEED: Final[tuple[tuple[str, dict[str, object]], ...]] = (
    (
        "The standard deduction for a single filer is $14,600 for tax year 2026.",
        {"jurisdiction": "CA"},
    ),
    (
        "For tax year 2026 the single filer standard deduction equals $14,600.",
        {"jurisdiction": "CA"},
    ),
    (
        "Federal ordinary-income brackets are marginal, so only income inside a band is "
        "taxed at that band's rate.",
        {"jurisdiction": "CA"},
    ),
    (
        "New York resident credit rules are computed on state taxable income.",
        {"jurisdiction": "NY"},
    ),
)


#: Token counts the stub reports, so a test can assert on the exact numbers that reach a
#: caller's budget rather than on "something non-zero".
STUB_INPUT_TOKENS: int = 1_200
STUB_OUTPUT_TOKENS: int = 300


class _StubMessages:
    """The ``.messages`` namespace of the Anthropic client, recording what it was asked."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        """Record the request and return one text block."""
        from anthropic.types import TextBlock, Usage

        self.calls.append(kwargs)
        return type(
            "_StubMessage",
            (),
            {
                "content": [TextBlock(text=STUB_ANSWER, type="text", citations=None)],
                # Carried so the pipeline's usage report is exercised rather than skipped by its
                # tolerant `getattr`. A stub without it would leave `answer["usage"]` untested
                # and the agent's budget silently unfed.
                "usage": Usage(input_tokens=STUB_INPUT_TOKENS, output_tokens=STUB_OUTPUT_TOKENS),
            },
        )()


class _StubAnthropic:
    """A stand-in for :class:`anthropic.Anthropic` exposing only ``.messages.create``."""

    def __init__(self) -> None:
        self.messages = _StubMessages()


@pytest.fixture(scope="module")
def pipeline_corpus(pg_dsn: str) -> str:
    """Seed this module's tenant. ``doc_id`` is namespaced per tenant, as W7 D2 requires."""
    model = SentenceTransformer(MODEL_NAME)
    vectors: NDArray[np.float32] = model.encode(
        [text for text, _ in SEED],
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)

    load_rows(
        pg_dsn,
        [
            CorpusRow(
                doc_id=f"{TENANT}-doc-{i:03d}",
                chunk_idx=0,
                chunk_text=text,
                embedding=vector,
                model_version=MODEL_NAME,
                tenant_id=TENANT,
                chunk_metadata=metadata,
                content_hash=content_hash(text),
            )
            for i, ((text, metadata), vector) in enumerate(zip(SEED, vectors, strict=True))
        ],
    )
    return pg_dsn


def test_the_flags_default_to_on_and_only_explicit_off_values_disable_a_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset flag is ON; only ``0``/``false``/``no``/``off`` turn a stage off.

    Defaulting to on is the safe direction: the four upgrades are the intended configuration,
    so a misspelled variable leaves the pipeline working rather than silently reverting it to
    the W7 D2 baseline - a regression that no test and no alert would catch, because nothing
    errors.
    """
    monkeypatch.delenv(RAG_USE_HYBRID_ENV, raising=False)
    assert flag_from_env(RAG_USE_HYBRID_ENV) is True

    for off in ("0", "false", "FALSE", "no", "off", " Off "):
        monkeypatch.setenv(RAG_USE_HYBRID_ENV, off)
        assert flag_from_env(RAG_USE_HYBRID_ENV) is False, off

    for on in ("1", "true", "yes", "anything-else", ""):
        monkeypatch.setenv(RAG_USE_MMR_ENV, on)
        assert flag_from_env(RAG_USE_MMR_ENV) is True, on


def test_the_full_pipeline_returns_the_documented_payload_and_caches_it(
    pipeline_corpus: str, redis_client: redis.Redis
) -> None:
    """All four stages on: citations carry the tenant, and the second call hits the cache.

    The tenant on each citation is not decoration - it is what the cache's defence-in-depth
    check reads on every subsequent hit, so an answer stored without it becomes a permanent
    miss. Asserting it here is asserting that the write path satisfies the read path.

    ``rerank_timed_out`` is asserted to be *present and boolean*, deliberately not to be
    ``False``. This test is about the payload's shape and the cache round-trip, and the value of
    that flag is a property of the CPU the suite happens to run on: the production path sends
    twenty ``(query, passage)`` pairs through ``bge-reranker-base`` under a 300 ms soft
    deadline, and commit 04d7158 recorded that eight pairs already breach that budget on a
    GitHub shared runner. Asserting ``False`` here was the same flake that commit removed from
    ``test_rerank.py::test_bge_rerank_lifts_the_gold_chunk_out_of_the_retrieval_tail``; it
    survived that cleanup in this file and was caught locally by running two suites at once,
    which is the same CPU starvation a shared runner produces.

    The fix there was ``timeout_ms=60_000``, which is not available here: ``retrieve_and_generate``
    has no timeout parameter - the deliverable pins its signature - and the budget cannot be
    monkeypatched either, because ``bge_rerank`` binds ``RERANK_TIMEOUT_MS`` as a default
    argument at definition time. So this test keeps the half of the claim that is deterministic
    (the field is in the documented payload, and it survives the cache round-trip via the
    ``again == answer`` comparison below) and leaves the timing assertion to
    ``test_rerank.py``, where the breach is forced with ``timeout_ms=1`` and is therefore
    deterministic on any hardware. A soft-failing deadline that fires is the intended behaviour
    of this stage, not a defect for this test to police.
    """
    client = _StubAnthropic()
    with psycopg.connect(pipeline_corpus) as conn:
        answer = retrieve_and_generate(
            "what is the standard deduction for a single filer",
            TENANT,
            anthropic=client,
            conn=conn,
            r=redis_client,
            use_hybrid=True,
            use_mmr=True,
            use_rerank=True,
            use_filter=False,
        )

        assert answer["text"] == STUB_ANSWER
        # Present and boolean, not False - see the docstring. `is False` here is a latency
        # assertion wearing a payload assertion's clothes.
        assert isinstance(answer["rerank_timed_out"], bool)
        citations = answer["citations"]
        assert isinstance(citations, list) and citations
        assert all(c["tenant_id"] == TENANT for c in citations)
        assert all(c["chunk_id"].startswith(f"chunk-{TENANT}-doc-") for c in citations)
        coverage = answer["coverage"]
        assert isinstance(coverage, dict)
        assert 0.0 <= coverage["jaccard"] <= 1.0

        # The second identical call is served from Redis: one generation call in total.
        again = retrieve_and_generate(
            "what is the standard deduction for a single filer",
            TENANT,
            anthropic=client,
            conn=conn,
            r=redis_client,
            use_hybrid=True,
            use_mmr=True,
            use_rerank=True,
            use_filter=False,
        )

    # Compared WITHOUT `usage`, because the two are deliberately not equal in that one field:
    # the generated answer reports the tokens it spent, the cached replay reports none, since a
    # Redis GET spends none. Caching the usage alongside the answer would make every hit bill
    # the tokens of the call that first produced it.
    assert {k: v for k, v in again.items() if k != "usage"} == {
        k: v for k, v in answer.items() if k != "usage"
    }
    assert answer["usage"] == {
        "input_tokens": STUB_INPUT_TOKENS,
        "output_tokens": STUB_OUTPUT_TOKENS,
    }
    assert "usage" not in again, "a cache hit reported tokens it never spent"
    assert len(client.messages.calls) == 1, "the cache did not serve the repeated question"


def test_with_every_flag_off_the_pipeline_reproduces_the_w7d2_shape(
    pipeline_corpus: str, redis_client: redis.Redis
) -> None:
    """Baseline mode: no sparse retrieval, no fusion, no MMR, no rerank.

    This is the report's baseline column and the A/B rollback target, so it has to be reachable
    by flags alone. The distinguishing observable is the citation score: with hybrid off the
    scores are cosine DISTANCES carried through from the dense query, not RRF scores - which is
    why the flag-off path deliberately does not fuse the dense list with an empty one.
    """
    client = _StubAnthropic()
    with psycopg.connect(pipeline_corpus) as conn:
        answer = retrieve_and_generate(
            "marginal brackets and taxable income",
            TENANT,
            anthropic=client,
            conn=conn,
            r=redis_client,
            use_hybrid=False,
            use_mmr=False,
            use_rerank=False,
            use_filter=False,
        )

    citations = answer["citations"]
    assert isinstance(citations, list) and citations
    # Cosine distance is in [0, 2]; an RRF score over a 60-window is under 1/61 ~= 0.016, so a
    # value above that is evidence the dense distances survived rather than being replaced.
    assert any(c["score"] > 0.02 for c in citations), citations
    coverage = answer["coverage"]
    assert isinstance(coverage, dict)
    # No sparse retrieval happened at all, so nothing is in both lists.
    assert coverage["sparse_only"] == 0.0
    assert coverage["both"] == 0.0


def test_the_metadata_filter_restricts_the_context_the_generator_sees(
    pipeline_corpus: str, redis_client: redis.Redis
) -> None:
    """``use_filter=True`` with a jurisdiction filter excludes the other jurisdiction's chunk.

    Asserted on the CITATIONS rather than on the prompt, because the citations are what a caller
    and the cache see. This is the compliance property the eval matrix cannot measure: on a
    single-jurisdiction golden set the filter moves no metric, and it is still what stops a
    California question being answered from a New York chunk.
    """
    client = _StubAnthropic()
    with psycopg.connect(pipeline_corpus) as conn:
        answer = retrieve_and_generate(
            "state taxable income and resident credit",
            TENANT,
            anthropic=client,
            conn=conn,
            r=redis_client,
            metadata_filter={"jurisdiction": "CA"},
            use_hybrid=False,
            use_mmr=False,
            use_rerank=False,
            use_filter=True,
        )

    citations = answer["citations"]
    assert isinstance(citations, list) and citations
    texts = " ".join(str(c["chunk_text"]) for c in citations)
    assert "New York" not in texts, texts
