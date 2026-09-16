# taxcalc-ai/src/taxcalc_ai/corpus.py
"""Pandas corpus loader + sentence-transformers embedding pass.

This module is the single seam between raw source documents and the pgvector corpus. Everything
downstream - the ANN retrieval in :mod:`taxcalc_ai.rag`, the Great Expectations suite, the RAGAS
baseline - reads rows that came through here, which is why the validation lives at this boundary
and not scattered across the callers.

Three disciplines are enforced here, and each one exists because of a specific failure mode:

**The embedding dtype is ``float32``, end to end, and it is pinned at the boundary.**
``SentenceTransformer.encode`` returns ``float64`` on some paths and ``float32`` on others
depending on the backend and the ``convert_to_numpy`` flag. pgvector's wire format is 4-byte
``real`` components, so a ``float64`` array is either rejected or silently narrowed on write -
and a silently narrowed vector is the worst outcome, because the insert succeeds and the
retrieval quality degrades with nothing in the logs to explain it. ``.astype(np.float32)`` is
applied once, here, and :class:`CorpusRow` declares ``NDArray[np.float32]`` so a caller that
constructs a row from a wider array is a type error rather than a runtime surprise.

**De-duplication on ``(doc_id, chunk_idx)`` happens before embedding, not after.**
``(doc_id, chunk_idx, model_version)`` is the uniqueness key the pgvector table enforces and the
key the loader's ``ON CONFLICT`` clause resolves on. Letting a duplicate through would mean
paying to embed a chunk twice and then having the second insert overwrite the first - the same
final state, reached by doing the expensive half of the work twice. Dropping duplicates first
makes the m:1 invariant (each ``(doc_id, chunk_idx)`` appears exactly once) hold at the point
where it is cheapest to enforce.

**Length bounds are a filter, not an assertion.** A zero-length chunk embeds to a vector that is
nearest-neighbour to nothing in particular, and an 8000+ character chunk is past what MiniLM's
256-token window reads - the tail is silently truncated, so the vector describes the first
paragraph and the retrieval claims to describe the whole chunk. Both are dropped rather than
raised on, because a corpus is a bulk input: one malformed row should not fail a 100k-row load.

**Encoding is batched, not per-row.** ``df.apply(lambda r: model.encode(r.chunk_text))`` is the
obvious shape and the wrong one: it pays the model's fixed per-call overhead once per row and
gives up the batched matrix multiply the library is built around. One ``encode(texts,
batch_size=...)`` call over the whole column is the same result, an order of magnitude faster.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sentence_transformers import SentenceTransformer

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.corpus")

#: The embedding model. Also the value written to ``doc_chunks.model_version``, which is what
#: makes a model swap a new set of rows rather than a silent mix of two vector geometries in
#: one index - see the ``WHERE model_version = %s`` filter in :mod:`taxcalc_ai.rag`.
MODEL_NAME: Final[str] = "all-MiniLM-L6-v2"

#: MiniLM output dimension; matches the ``vector(384)`` column in ``sql/V001__doc_chunks.sql``.
#: The column's dimension is fixed at DDL time and cannot be altered in place with data present,
#: so these two constants have to move together or not at all.
EMBEDDING_DIM: Final[int] = 384

#: Chunk length bounds, in characters. The upper bound is a deliberate over-estimate of MiniLM's
#: 256-token window (~4 chars/token): past it, ``encode`` truncates silently.
MIN_CHUNK_CHARS: Final[int] = 1
MAX_CHUNK_CHARS: Final[int] = 8000

#: Columns a corpus file must carry. Checked before any expensive work happens.
REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {"doc_id", "chunk_idx", "chunk_text", "tenant_id"}
)


@dataclass(frozen=True, slots=True)
class CorpusRow:
    """One chunk plus its embedding, ready for a pgvector insert.

    Frozen and slotted for the reasons set out in :mod:`taxcalc_ai.value_types`: value semantics,
    a fixed field set, and no ``__dict__`` for a typo'd attribute to land in.

    The field order is the order the ``INSERT`` in :mod:`taxcalc_ai.pgvector_loader` binds its
    parameters, which is deliberate - the loader builds its payload tuples positionally, and a
    reordering here without a matching change there would bind ``tenant_id`` into the
    ``model_version`` column without any type error to catch it.

    :param doc_id: Source document identifier. A synthetic, prefixed string per the project's
        identifier convention - never a bare integer.
    :param chunk_idx: Position of this chunk within its document, 0-based.
    :param chunk_text: The chunk itself, already length-validated by :func:`load_corpus`.
    :param embedding: The MiniLM vector, ``float32`` and :data:`EMBEDDING_DIM` long.
    :param model_version: The model that produced ``embedding``; written to the table so a
        query can refuse to rank vectors from two different models against each other.
    :param tenant_id: Owning tenant. Every read path filters on this before ranking by distance.
    """

    doc_id: str
    chunk_idx: int
    chunk_text: str
    embedding: NDArray[np.float32]
    model_version: str
    tenant_id: str


def load_corpus(path: Path) -> pd.DataFrame:
    """Read a parquet or jsonl corpus into a validated :class:`~pandas.DataFrame`.

    The returned frame is guaranteed to carry :data:`REQUIRED_COLUMNS`, to hold at most one row
    per ``(doc_id, chunk_idx)``, and to contain only chunks between :data:`MIN_CHUNK_CHARS` and
    :data:`MAX_CHUNK_CHARS` characters inclusive. The index is reset, so positional access in
    :func:`embed_dataframe` lines up with the row order.

    :param path: Corpus file. The extension selects the reader: ``.parquet`` for columnar input,
        ``.jsonl``/``.json`` for line-delimited JSON.
    :returns: The validated frame, with a fresh ``RangeIndex``.
    :raises ValueError: if the extension is not one of the supported ones, or if any of
        :data:`REQUIRED_COLUMNS` is absent. Both are raised rather than filtered because they
        indicate the wrong *file*, not a bad row - continuing would embed a corpus nobody meant
        to load.
    """
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix in (".jsonl", ".json"):
        df = pd.read_json(path, lines=True)
    else:
        raise ValueError(f"unsupported corpus extension: {path.suffix}")

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"corpus missing columns: {sorted(missing)}")

    # m:1 invariant: each (doc_id, chunk_idx) row appears exactly once. keep="first" makes the
    # choice explicit - a re-export that appends rather than replaces puts the stale copy last.
    before = len(df)
    df = df.drop_duplicates(subset=["doc_id", "chunk_idx"], keep="first")
    dropped_duplicates = before - len(df)
    if dropped_duplicates:
        _LOG.info(
            "corpus.load.deduplicated",
            extra={"event": "corpus.load.deduplicated", "dropped": dropped_duplicates},
        )

    # engine="python" so `chunk_text.str.len()` resolves: the default numexpr engine cannot
    # call into pandas string accessors, and the chained comparison is evaluated as one
    # expression rather than two boolean masks.
    before = len(df)
    df = df.query(
        f"{MIN_CHUNK_CHARS} <= chunk_text.str.len() <= {MAX_CHUNK_CHARS}", engine="python"
    )
    dropped_length = before - len(df)
    if dropped_length:
        _LOG.info(
            "corpus.load.length_filtered",
            extra={"event": "corpus.load.length_filtered", "dropped": dropped_length},
        )

    return df.reset_index(drop=True)


def embed_dataframe(
    df: pd.DataFrame,
    model: SentenceTransformer | None = None,
    batch_size: int = 64,
) -> list[CorpusRow]:
    """Embed every row's ``chunk_text`` in batches and return the rows ready for insert.

    :param df: A frame from :func:`load_corpus`. Passing an unvalidated frame is a caller error;
        this function does not re-check the column set.
    :param model: A loaded :class:`~sentence_transformers.SentenceTransformer`. ``None`` loads
        :data:`MODEL_NAME`. Tests and batch jobs pass one in, because constructing the model is
        the expensive part (~80 MB of weights) and paying it once per call turns a fast function
        into a slow one for reasons invisible at the call site.
    :param batch_size: Rows per forward pass. Trades peak memory against throughput.
    :returns: One :class:`CorpusRow` per input row, in input order, each carrying a ``float32``
        vector of length :data:`EMBEDDING_DIM`.
    """
    m = model if model is not None else SentenceTransformer(MODEL_NAME)
    texts: list[str] = df["chunk_text"].tolist()

    # normalize_embeddings=True makes every vector unit length, which is what lets cosine
    # distance stand in for angular distance without a per-query normalisation step - the HNSW
    # index is built over the stored vectors, so normalising at write time is the only place
    # it can happen once rather than on every read.
    # One batched encode replaces a Python per-row loop; see the module docstring.
    vectors: NDArray[np.float32] = m.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)

    rows: list[CorpusRow] = []
    for idx, vec in enumerate(vectors):
        record = df.iloc[idx]
        rows.append(
            CorpusRow(
                doc_id=str(record["doc_id"]),
                chunk_idx=int(record["chunk_idx"]),
                chunk_text=str(record["chunk_text"]),
                embedding=vec,
                model_version=MODEL_NAME,
                tenant_id=str(record["tenant_id"]),
            )
        )
    return rows
