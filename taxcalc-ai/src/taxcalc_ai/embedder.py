# taxcalc-ai/src/taxcalc_ai/embedder.py
"""The idempotent re-embed gate: skip the model call, not just the write.

W7 D2 made the *write* idempotent - ``ON CONFLICT (doc_id, chunk_idx, model_version) DO UPDATE``
means re-running a load is a no-op for unchanged rows. It did not make the re-run cheap. The
embedding that the ``DO UPDATE`` overwrites the old one with still had to be computed, so a daily
ingest over a corpus that changed in three places paid to re-embed all of it. On a 100k-chunk
corpus that is the entire cost of the pipeline, spent to arrive at the state it was already in.

**The gate is a single SELECT, run before any encoding.** For each incoming
``(doc_id, chunk_idx)`` at this ``model_version``, the stored ``content_hash`` is compared
against the hash of the incoming text. Equal means the row in the database is already the
vector this run would produce, so the chunk is dropped from the batch entirely. Unequal - or
absent, which is every legacy V001 row and every genuinely new chunk - means embed.

**``model_version`` is part of the comparison, not an afterthought.** A chunk whose text is
unchanged but whose model has been swapped needs a *new* vector, not a skip: the two models'
outputs occupy the same 384-dimensional space without meaning the same thing. Keying the gate
on ``(doc_id, chunk_idx, model_version)`` - the table's own uniqueness key - makes a model swap
re-embed everything, which is the correct and expensive answer, while a text-only change
re-embeds only what moved.

**A ``NULL`` stored hash is treated as "unknown", never as "matches".** Rows written before
``sql/V002`` have no hash. They compare unequal to every incoming hash and are re-embedded once,
after which they carry one. The alternative - defaulting the column to ``''`` - would have the
gate assert that a row's content hashes to the empty string, and it would then skip forever.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import numpy as np
import psycopg
from langchain_core.documents import Document
from numpy.typing import NDArray
from sentence_transformers import SentenceTransformer

from .chunker import CHUNK_ORDINAL_KEY, DOC_ID_KEY, UNKNOWN_DOC_ID
from .corpus import MODEL_NAME, CorpusRow, content_hash
from .pgvector_loader import load_rows

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.embedder")

#: Default tenant for a chunk whose metadata carries none. ``shared`` rather than a real tenant:
#: mislabelling content into ``tenant-a`` would make it retrievable by that tenant, which is the
#: one failure this project treats as unacceptable.
DEFAULT_TENANT_ID: Final[str] = "shared"

#: Metadata key the tenant is read from.
TENANT_ID_KEY: Final[str] = "tenant_id"

#: The gate query. Returns the stored hash for every incoming key at this model version in one
#: round trip - ``= ANY(%s)`` over arrays rather than a row-at-a-time lookup, because the whole
#: point of the gate is to cost less than the work it avoids.
_STORED_HASHES_SQL: Final[str] = (
    "SELECT doc_id, chunk_idx, content_hash FROM doc_chunks "
    "WHERE model_version = %s AND doc_id = ANY(%s) AND chunk_idx = ANY(%s)"
)


@dataclass(frozen=True, slots=True)
class ChunkCandidate:
    """One chunk offered for ingestion, before anything has been embedded.

    Distinct from :class:`~taxcalc_ai.corpus.CorpusRow` precisely because it has no vector yet:
    the gate's job is to decide which candidates ever become rows, and a type that carried an
    ``embedding`` field would have to be constructed with a placeholder to be passed to it.

    :param doc_id: Source document identifier.
    :param chunk_idx: 0-based position within that document - the chunker's ``chunk_ordinal``
        under the column name ``sql/V001__doc_chunks.sql`` uses.
    :param chunk_text: The chunk itself.
    :param tenant_id: Owning tenant.
    :param chunk_metadata: JSONB payload to store alongside the chunk.
    """

    doc_id: str
    chunk_idx: int
    chunk_text: str
    tenant_id: str
    chunk_metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """SHA-256 of :attr:`chunk_text` - the value the gate compares and the loader stores."""
        return content_hash(self.chunk_text)


def candidates_from_chunks(chunks: Iterable[Document]) -> list[ChunkCandidate]:
    """Convert :func:`taxcalc_ai.chunker.chunk_docs` output into gate input.

    The bridge between the two halves of the ingest: the chunker speaks LangChain
    :class:`~langchain_core.documents.Document`, the corpus speaks rows. Keeping the conversion
    here - rather than having the chunker emit rows - is what lets the chunker stay a pure text
    function with no database or model dependency.

    Every metadata key survives into ``chunk_metadata``, including ``chunk_id`` and
    ``chunk_ordinal``, so the stored JSONB is a faithful record of what the chunker decided.

    :param chunks: Chunked documents carrying ``doc_id`` and ``chunk_ordinal`` metadata.
    :returns: One candidate per chunk, in input order.
    """
    return [
        ChunkCandidate(
            doc_id=str(chunk.metadata.get(DOC_ID_KEY, UNKNOWN_DOC_ID)),
            chunk_idx=int(chunk.metadata.get(CHUNK_ORDINAL_KEY, 0)),
            chunk_text=chunk.page_content,
            tenant_id=str(chunk.metadata.get(TENANT_ID_KEY, DEFAULT_TENANT_ID)),
            chunk_metadata=dict(chunk.metadata),
        )
        for chunk in chunks
    ]


def pending_candidates(
    dsn: str,
    candidates: Sequence[ChunkCandidate],
    model_version: str = MODEL_NAME,
) -> list[ChunkCandidate]:
    """Return the subset of ``candidates`` whose stored vector is not already current.

    This is the gate. It runs one query and no model calls, so the cost of asking is a single
    round trip regardless of how much work it saves.

    :param dsn: libpq connection string for the corpus database.
    :param candidates: The chunks offered for ingestion.
    :param model_version: The model this run would embed with. Part of the comparison key - see
        the module docstring for why a model swap must not be skippable.
    :returns: The candidates that need embedding, in input order. A candidate whose stored hash
        is ``NULL`` or differs is included; one whose stored hash matches is not.
    """
    if not candidates:
        return []

    doc_ids = [c.doc_id for c in candidates]
    chunk_idxs = [c.chunk_idx for c in candidates]
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(_STORED_HASHES_SQL, (model_version, doc_ids, chunk_idxs))
        # The `= ANY` pair is a cross product rather than a tuple match, so it can return keys
        # this batch does not contain. Building the map from the rows and then looking each
        # candidate up by its own key is what makes that harmless.
        stored: dict[tuple[str, int], str | None] = {
            (str(row[0]), int(row[1])): row[2] for row in cur.fetchall()
        }

    pending = [c for c in candidates if stored.get((c.doc_id, c.chunk_idx)) != c.content_hash]
    _LOG.info(
        "embedder.gate.evaluated",
        extra={
            "event": "embedder.gate.evaluated",
            "model_version": model_version,
            "offered": len(candidates),
            "pending": len(pending),
            "skipped": len(candidates) - len(pending),
        },
    )
    return pending


def embed_pending(
    dsn: str,
    candidates: Sequence[ChunkCandidate],
    model: SentenceTransformer | None = None,
    model_version: str = MODEL_NAME,
    batch_size: int = 64,
) -> int:
    """Run the gate, embed only what survives it, and upsert the result.

    The ordering is the entire optimisation: the gate query precedes the model construction as
    well as the encode, so a run over an unchanged corpus never loads ~80 MB of weights either.

    :param dsn: libpq connection string.
    :param candidates: The chunks offered for ingestion.
    :param model: A loaded model, or ``None`` to construct :data:`~taxcalc_ai.corpus.MODEL_NAME`
        lazily - and only if the gate left something to embed.
    :param model_version: Written to ``doc_chunks.model_version`` and used as the gate key.
    :param batch_size: Rows per forward pass.
    :returns: The number of rows written. Zero means everything was already current, which is
        the success case for a re-run, not a failure.
    """
    pending = pending_candidates(dsn, candidates, model_version=model_version)
    if not pending:
        _LOG.info(
            "embedder.embed.nothing_pending",
            extra={"event": "embedder.embed.nothing_pending", "offered": len(candidates)},
        )
        return 0

    encoder = model if model is not None else SentenceTransformer(model_version)
    # One batched encode over the whole pending set, for the reason
    # taxcalc_ai.corpus.embed_dataframe documents: a per-row encode pays the model's fixed
    # per-call overhead once per row and gives up the batched matrix multiply.
    vectors: NDArray[np.float32] = encoder.encode(
        [c.chunk_text for c in pending],
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)

    rows = [
        CorpusRow(
            doc_id=candidate.doc_id,
            chunk_idx=candidate.chunk_idx,
            chunk_text=candidate.chunk_text,
            embedding=vector,
            model_version=model_version,
            tenant_id=candidate.tenant_id,
            chunk_metadata=candidate.chunk_metadata,
            content_hash=candidate.content_hash,
        )
        for candidate, vector in zip(pending, vectors, strict=True)
    ]
    return load_rows(dsn, rows)
