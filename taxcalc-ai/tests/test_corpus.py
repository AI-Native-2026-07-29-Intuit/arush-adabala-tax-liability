# taxcalc-ai/tests/test_corpus.py
"""Tests for the Pandas corpus loader and the embedding pass.

The three behaviours pinned here are the ones the rest of Week 7 assumes without re-checking:
de-duplication on ``(doc_id, chunk_idx)``, the length-bounds filter, and the ``float32``
embedding dtype at the boundary. Each has a failure mode that is silent rather than loud - a
duplicate that survives becomes an ``ON CONFLICT`` overwrite, an over-long chunk becomes a
vector describing only its first paragraph, and a ``float64`` array becomes malformed bytes in
the ``vector(384)`` column - so none of them shows up as an exception in a later test.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from numpy.typing import NDArray
from sentence_transformers import SentenceTransformer

from taxcalc_ai.corpus import (
    EMBEDDING_DIM,
    MAX_CHUNK_CHARS,
    MODEL_NAME,
    CorpusRow,
    embed_dataframe,
    load_corpus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "corpus_seed.jsonl"


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> Path:
    """Write ``records`` as line-delimited JSON and return the path, for ``load_corpus``."""
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


class _StubModel:
    """A stand-in for :class:`~sentence_transformers.SentenceTransformer`.

    Deliberately returns ``float64`` - the dtype a real backend sometimes hands back. That is
    the entire point: the stub proves :func:`~taxcalc_ai.corpus.embed_dataframe` narrows at the
    boundary rather than passing whatever the model produced through to pgvector. A stub that
    already returned ``float32`` would make the assertion pass without testing anything.
    """

    def encode(
        self,
        sentences: list[str],
        batch_size: int = 32,
        normalize_embeddings: bool = False,
        convert_to_numpy: bool = True,
    ) -> NDArray[np.float64]:
        """Return one deterministic unit-length ``float64`` row per input sentence."""
        out = np.zeros((len(sentences), EMBEDDING_DIM), dtype=np.float64)
        for i in range(len(sentences)):
            out[i, i % EMBEDDING_DIM] = 1.0
        return out


def test_load_corpus_drops_duplicate_doc_id_chunk_idx(tmp_path: Path) -> None:
    """Two rows sharing ``(doc_id, chunk_idx)`` collapse to one, and the first wins.

    ``keep="first"`` is asserted explicitly, not just the row count: a re-export that appends
    instead of replacing puts the stale copy second, so "which one survived" is the difference
    between the current text and an old one.
    """
    path = _write_jsonl(
        tmp_path / "dupes.jsonl",
        [
            {"doc_id": "taxpayer-001", "chunk_idx": 0, "chunk_text": "kept", "tenant_id": "t-a"},
            {"doc_id": "taxpayer-001", "chunk_idx": 0, "chunk_text": "dropped", "tenant_id": "t-a"},
            {"doc_id": "taxpayer-001", "chunk_idx": 1, "chunk_text": "other", "tenant_id": "t-a"},
            {"doc_id": "taxpayer-002", "chunk_idx": 0, "chunk_text": "another", "tenant_id": "t-b"},
        ],
    )

    df = load_corpus(path)

    assert len(df) == 3
    assert df["chunk_text"].tolist() == ["kept", "other", "another"]
    # The index is reset, so positional access in embed_dataframe lines up with row order.
    assert df.index.tolist() == [0, 1, 2]


def test_load_corpus_rejects_rows_outside_length_bounds(tmp_path: Path) -> None:
    """Chunks shorter than 1 or longer than 8000 characters are filtered out, not raised on.

    A corpus is a bulk input: one malformed row must not fail a whole load. Both boundary
    values are included so the filter is pinned as inclusive - an off-by-one here silently
    drops every chunk of exactly the maximum length.
    """
    path = _write_jsonl(
        tmp_path / "lengths.jsonl",
        [
            {"doc_id": "d-1", "chunk_idx": 0, "chunk_text": "", "tenant_id": "t-a"},
            {"doc_id": "d-2", "chunk_idx": 0, "chunk_text": "x", "tenant_id": "t-a"},
            {
                "doc_id": "d-3",
                "chunk_idx": 0,
                "chunk_text": "y" * MAX_CHUNK_CHARS,
                "tenant_id": "t-a",
            },
            {
                "doc_id": "d-4",
                "chunk_idx": 0,
                "chunk_text": "z" * (MAX_CHUNK_CHARS + 1),
                "tenant_id": "t-a",
            },
        ],
    )

    df = load_corpus(path)

    assert df["doc_id"].tolist() == ["d-2", "d-3"]


def test_embed_dataframe_returns_float32_vectors_of_the_model_dimension() -> None:
    """Every row carries a ``float32`` vector of shape ``(384,)`` - the pgvector contract.

    Run against the real MiniLM model, because the assertion that matters is that
    :data:`~taxcalc_ai.corpus.EMBEDDING_DIM` and the model's actual output geometry agree. A
    stub could not catch a model swap that changed the dimension, which is the failure this
    guards: ``vector(384)`` is fixed at DDL time and cannot be altered in place with data
    present.
    """
    df = load_corpus(FIXTURE).head(4)

    rows = embed_dataframe(df, model=SentenceTransformer(MODEL_NAME), batch_size=2)

    assert len(rows) == 4
    for row in rows:
        assert isinstance(row, CorpusRow)
        assert row.embedding.dtype == np.float32
        assert row.embedding.shape == (EMBEDDING_DIM,)
        assert row.model_version == MODEL_NAME
        # normalize_embeddings=True is what lets cosine distance stand in for angular distance
        # without a per-query normalisation step.
        assert np.isclose(float(np.linalg.norm(row.embedding)), 1.0, atol=1e-5)


def test_embed_dataframe_narrows_a_float64_model_to_float32() -> None:
    """A model returning ``float64`` is narrowed at the boundary rather than passed through.

    This is the half of the dtype contract the real model cannot test, because it already
    returns ``float32`` on this backend. pgvector stores 4-byte ``real`` components, so a
    ``float64`` array reaching the insert is either rejected or silently narrowed - and the
    silent case is worse, since the write succeeds and retrieval quality degrades with nothing
    in the logs to explain it.
    """
    df = load_corpus(FIXTURE).head(3)

    rows = embed_dataframe(df, model=_StubModel())  # type: ignore[arg-type]

    assert [r.embedding.dtype for r in rows] == [np.dtype(np.float32)] * 3


def test_embed_dataframe_preserves_row_order_and_identifiers() -> None:
    """Row *i* of the output describes row *i* of the input.

    ``embed_dataframe`` pairs vectors to rows by position, so a reordering anywhere between the
    encode call and the loop would attach one chunk's text to another chunk's vector - a defect
    with no exception and no wrong-looking data, only wrong search results.
    """
    df = load_corpus(FIXTURE).head(5)

    rows = embed_dataframe(df, model=_StubModel())  # type: ignore[arg-type]

    assert [r.doc_id for r in rows] == df["doc_id"].tolist()
    assert [r.chunk_idx for r in rows] == df["chunk_idx"].tolist()
    assert [r.chunk_text for r in rows] == df["chunk_text"].tolist()
    assert [r.tenant_id for r in rows] == df["tenant_id"].tolist()


def test_load_corpus_raises_on_missing_required_columns(tmp_path: Path) -> None:
    """A frame without ``tenant_id`` is the wrong file, not a bad row, so it raises."""
    path = _write_jsonl(
        tmp_path / "no_tenant.jsonl",
        [{"doc_id": "d-1", "chunk_idx": 0, "chunk_text": "text"}],
    )

    with pytest.raises(ValueError, match=r"corpus missing columns: \['tenant_id'\]"):
        load_corpus(path)


def test_load_corpus_raises_on_unsupported_extension(tmp_path: Path) -> None:
    """An extension the loader has no reader for raises rather than guessing a format."""
    path = tmp_path / "corpus.csv"
    path.write_text("doc_id,chunk_idx\n")

    with pytest.raises(ValueError, match=r"unsupported corpus extension: \.csv"):
        load_corpus(path)


def test_committed_fixture_is_a_clean_hundred_row_corpus() -> None:
    """The committed seed carries exactly the shape every later W7 D2 test assumes.

    The Great Expectations suite asserts at least 100 rows land in ``doc_chunks`` and the
    pgvector loader test asserts a load of 100 returns 100. Both read this file, so its row
    count is a contract rather than an incidental property of the generator.

    The identifier range is asserted too, not just the count. ``taxpayer-001`` through
    ``taxpayer-100``, contiguous and one chunk each, is the shape the brief specifies; a
    regenerated fixture that packed the same 100 rows into fewer multi-chunk documents would
    still satisfy every other assertion here, which is exactly why this one is spelled out.
    """
    df = load_corpus(FIXTURE)

    assert len(df) == 100
    assert set(df["tenant_id"].unique()) == {"tenant-a", "tenant-b", "tenant-c"}
    # No row was dropped by de-dup or the length filter: the committed file is already clean.
    raw = pd.read_json(FIXTURE, lines=True)
    assert len(raw) == 100
    # Distinct text on every row; near-duplicate context is what the golden set tests against.
    assert df["chunk_text"].nunique() == 100
    # One chunk per document, numbered taxpayer-001..taxpayer-100 with no gaps.
    assert sorted(df["doc_id"]) == [f"taxpayer-{n:03d}" for n in range(1, 101)]
    assert set(df["chunk_idx"].unique()) == {0}
