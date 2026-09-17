# taxcalc-ai/tests/test_chunker.py
"""Chunking discipline: the overlap guard, id stability, and the size distribution.

No container and no model here - chunking is pure text work, and these three properties are the
ones that decide what the retrieval layer can ever find. They are asserted in isolation so a
regression names "the chunker changed" rather than surfacing two layers away as a RAGAS score
that drifted.
"""

from __future__ import annotations

import pytest
from langchain_core.documents import Document

from taxcalc_ai.chunker import (
    CHUNK_ID_KEY,
    CHUNK_ORDINAL_KEY,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    SEPARATORS,
    UNKNOWN_DOC_ID,
    chunk_docs,
    chunk_id_for,
    make_splitter,
)

#: One paragraph of prose, repeated to build documents of a realistic size. Sentence-terminated
#: and blank-line separated so the coarse end of the separator ladder is what actually does the
#: splitting - a wall of undifferentiated characters would only exercise the ``""`` fallback.
PARAGRAPH = (
    "Federal ordinary-income brackets are marginal, so only the income falling inside a band "
    "is taxed at that band's rate. Reaching the top bracket does not retax the income below "
    "it. The standard deduction is subtracted from adjusted gross income to reach taxable "
    "income before the bracket ladder is applied at all.\n\n"
)

#: Chunk-length window the distribution assertion holds to. The floor is the interesting half:
#: a splitter that emitted mostly tiny fragments would still satisfy an upper bound while
#: destroying retrieval quality, because a 40-character chunk embeds to a vector that is near
#: nothing in particular.
MIN_MEAN_CHUNK_CHARS = 400
MAX_MEAN_CHUNK_CHARS = 950


def _document(doc_id: str, repeats: int, tenant_id: str = "tenant-a") -> Document:
    """A document of roughly ``repeats * len(PARAGRAPH)`` characters."""
    return Document(
        page_content=PARAGRAPH * repeats,
        metadata={"doc_id": doc_id, "tenant_id": tenant_id},
    )


def test_make_splitter_rejects_an_overlap_at_or_above_half_the_chunk_size() -> None:
    """``make_splitter(chunk_size=100, overlap=200)`` raises rather than clamping.

    This is the Topic 3 watch-out. The splitter advances by ``chunk_size - overlap``, so an
    overlap at or above the chunk size cannot make progress at all, and anything above half
    duplicates the majority of every chunk into its neighbour. Both are caller bugs that
    produce a plausible-looking corpus, which is why they fail loudly here.
    """
    with pytest.raises(ValueError, match="overlap must satisfy"):
        make_splitter(chunk_size=100, overlap=200)

    # The boundary itself, not just the obviously-wrong value: exactly half is excluded, and an
    # off-by-one in the guard would let it through while still rejecting 200.
    with pytest.raises(ValueError, match="overlap must satisfy"):
        make_splitter(chunk_size=100, overlap=50)

    # A negative overlap is meaningless rather than merely wasteful, and is rejected by the same
    # bound - the lower half of which a `overlap < chunk_size // 2` check alone would miss.
    with pytest.raises(ValueError, match="overlap must satisfy"):
        make_splitter(chunk_size=100, overlap=-1)

    # And the legal case still builds, so the guard is a bound rather than a blanket refusal.
    splitter = make_splitter(chunk_size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_OVERLAP)
    assert splitter._separators == list(SEPARATORS)


def test_chunk_ids_are_stable_and_monotonic_in_chunk_ordinal() -> None:
    """A 5 KB document yields ids that match the documented pattern and renumber per document.

    Two properties, and the second is the one that matters for citations: re-chunking the same
    bytes must reproduce the same ids, because a citation stored in an answer - or in the
    semantic cache - resolves by id. Ordinals restart at 0 for each ``doc_id``, so a change to
    one document cannot renumber another.
    """
    doc = _document("taxpayer-001", repeats=16)  # ~5 KB
    assert 4_500 <= len(doc.page_content) <= 6_000, len(doc.page_content)

    chunks = chunk_docs([doc])

    assert len(chunks) > 1, "a 5 KB document should not fit in one 900-character chunk"
    ordinals = [chunk.metadata[CHUNK_ORDINAL_KEY] for chunk in chunks]
    assert ordinals == list(range(len(chunks))), ordinals
    assert [chunk.metadata[CHUNK_ID_KEY] for chunk in chunks] == [
        chunk_id_for("taxpayer-001", i) for i in range(len(chunks))
    ]

    # Stability: the same input re-chunked produces the same ids, byte for byte.
    assert [c.metadata[CHUNK_ID_KEY] for c in chunk_docs([_document("taxpayer-001", 16)])] == [
        c.metadata[CHUNK_ID_KEY] for c in chunks
    ]

    # Per-document numbering: a second document starts its own ordinals at 0 rather than
    # continuing the first one's count.
    two = chunk_docs([_document("taxpayer-001", 16), _document("taxpayer-002", 14)])
    second = [c for c in two if c.metadata["doc_id"] == "taxpayer-002"]
    assert [c.metadata[CHUNK_ORDINAL_KEY] for c in second] == list(range(len(second)))

    # Caller metadata survives: the splitter copies it and chunk_docs only adds its own keys,
    # which is what puts tenant_id into doc_chunks.chunk_metadata for the pre-filter to use.
    assert {c.metadata["tenant_id"] for c in chunks} == {"tenant-a"}


def test_mean_chunk_length_sits_inside_the_expected_window() -> None:
    """Average chunk length for a typical 4-8 KB input lands in [400, 950].

    A distribution assertion rather than a per-chunk one, deliberately: the last chunk of any
    document is a remainder and is legitimately short, so a per-chunk floor would fail on
    correct output. The mean is what detects the real regressions - a separator list that
    stopped matching, or a chunk_size that was changed without anyone re-reading what the
    embedding model's window actually is.
    """
    for repeats in (13, 26):  # ~4 KB and ~8 KB
        doc = _document(f"taxpayer-{repeats:03d}", repeats=repeats)
        assert 3_500 <= len(doc.page_content) <= 9_000, len(doc.page_content)

        chunks = chunk_docs([doc])
        lengths = [len(chunk.page_content) for chunk in chunks]
        mean = sum(lengths) / len(lengths)

        assert MIN_MEAN_CHUNK_CHARS <= mean <= MAX_MEAN_CHUNK_CHARS, (mean, lengths)
        # No chunk over budget: the trailing "" separator guarantees a hard cut is always
        # available, so nothing should be emitted above chunk_size.
        assert max(lengths) <= DEFAULT_CHUNK_SIZE, lengths


def test_a_document_without_a_doc_id_is_chunked_under_the_synthetic_id() -> None:
    """A missing ``doc_id`` degrades to :data:`UNKNOWN_DOC_ID` instead of dropping the document.

    A bulk ingest should not silently lose content over a missing label. The synthetic id is
    obviously synthetic, so the gap is legible in a citation rather than invisible.
    """
    chunks = chunk_docs([Document(page_content=PARAGRAPH * 6, metadata={})])

    assert chunks
    assert {c.metadata["doc_id"] for c in chunks} == {UNKNOWN_DOC_ID}
    assert chunks[0].metadata[CHUNK_ID_KEY] == chunk_id_for(UNKNOWN_DOC_ID, 0)
