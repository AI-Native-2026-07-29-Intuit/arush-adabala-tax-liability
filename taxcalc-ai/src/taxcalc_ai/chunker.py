# taxcalc-ai/src/taxcalc_ai/chunker.py
"""Recursive character chunking: the seam between raw documents and the embedder.

W7 D2 loaded a corpus that arrived pre-chunked, one row per ``(doc_id, chunk_idx)``. Real
documents do not arrive that way, and how they are split decides what retrieval can ever find:
a chunk that straddles two unrelated topics dilutes its own embedding, and a chunk cut through
the middle of a sentence loses the clause that made it answerable.

**The separator ladder is ordered coarse-to-fine, and the order is the whole point.**
:class:`~langchain_text_splitters.RecursiveCharacterTextSplitter` tries ``"\\n\\n"`` first and
only falls to the next separator when a piece is still over budget - paragraph, then line, then
sentence, then word, then (last resort) a hard character cut. The final ``""`` is what
guarantees termination: without it, a single 5000-character run with no whitespace cannot be
split and is emitted over budget.

**``overlap < chunk_size / 2`` is validated, not assumed, and this is the Topic 3 watch-out.**
The splitter advances by ``chunk_size - overlap`` per step. At ``overlap == chunk_size`` the
stride is zero and the splitter never terminates; at ``overlap > chunk_size / 2`` every chunk
is more than half a copy of its neighbour, so the corpus grows super-linearly and retrieval
returns the same passage under several ids. :func:`make_splitter` raises on both rather than
letting a plausible-looking pair of numbers produce a corpus twice the size anyone expected.

**Chunk ids are synthetic, deterministic, and per-document.** ``chunk-{doc_id}-p{ordinal}``
with the ordinal restarting at 0 for each document means re-ingesting an unchanged document
produces byte-identical ids, so citations stored in an answer - or in the semantic cache - stay
resolvable across re-ingestion. A monotonic *global* counter would be equally unique and would
renumber every document the moment one document ahead of it in the batch gained a paragraph.
The per-document ordinal is also exactly the ``chunk_idx`` column ``sql/V001__doc_chunks.sql``
already keys on, so the two identifiers cannot drift apart.
"""

from __future__ import annotations

import logging
from typing import Final

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.chunker")

#: Target chunk size in characters. 900 is a little under MiniLM's 256-token window
#: (~4 chars/token) so a full chunk is embedded rather than silently truncated at the tail -
#: the failure :mod:`taxcalc_ai.corpus` describes on its ``MAX_CHUNK_CHARS`` filter.
DEFAULT_CHUNK_SIZE: Final[int] = 900

#: Characters of overlap between adjacent chunks: one sentence-ish of carried context, so a fact
#: split across a boundary survives in at least one chunk whole. 150/900 is a 1/6 stride tax,
#: comfortably inside the ``chunk_size / 2`` ceiling :func:`make_splitter` enforces.
DEFAULT_OVERLAP: Final[int] = 150

#: The coarse-to-fine ladder. See the module docstring; the trailing ``""`` is the termination
#: guarantee and must stay last.
SEPARATORS: Final[tuple[str, ...]] = ("\n\n", "\n", ". ", " ", "")

#: Metadata key carrying the synthetic chunk id. Read by the retrievers (which reconstruct the
#: same string in SQL) and by the citation payload in :mod:`taxcalc_ai.rag`, so it is a contract
#: rather than a label.
CHUNK_ID_KEY: Final[str] = "chunk_id"

#: Metadata key carrying the 0-based position of a chunk within its own document.
CHUNK_ORDINAL_KEY: Final[str] = "chunk_ordinal"

#: Metadata key the source document identifier is read from. A document that arrives without one
#: is chunked under :data:`UNKNOWN_DOC_ID` rather than dropped - see :func:`chunk_docs`.
DOC_ID_KEY: Final[str] = "doc_id"

#: Stand-in ``doc_id`` for a document that carries none. Deliberately prefixed and obviously
#: synthetic: it is legible in a citation as "this came in without provenance", which a blank
#: or a UUID would not be.
UNKNOWN_DOC_ID: Final[str] = "doc-synth-unknown"


def chunk_id_for(doc_id: str, ordinal: int) -> str:
    """Build the synthetic chunk id for one chunk.

    Exposed as a function rather than inlined at its two call sites because the retrievers in
    :mod:`taxcalc_ai.hybrid` rebuild the same string in SQL (``'chunk-' || doc_id || '-p' ||
    chunk_idx``). Two places construct this identifier and they must agree; a named function is
    where the format is stated once and where a test can pin it.

    :param doc_id: Source document identifier.
    :param ordinal: 0-based position of the chunk within that document.
    :returns: The chunk id, e.g. ``chunk-taxpayer-001-p0``.
    """
    return f"chunk-{doc_id}-p{ordinal}"


def make_splitter(
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> RecursiveCharacterTextSplitter:
    """Build the configured splitter, refusing an overlap that cannot terminate or dedupe.

    :param chunk_size: Target chunk length in characters.
    :param overlap: Characters each chunk repeats from its predecessor.
    :returns: A splitter over :data:`SEPARATORS` measuring length in characters (``len``), not
        tokens - the column and the model window are both reasoned about in characters here, and
        a tokeniser-based length function would make the bound depend on which tokeniser.
    :raises ValueError: unless ``0 <= overlap < chunk_size / 2``. Raised rather than clamped: a
        caller that asked for a 200-character overlap on 100-character chunks has a bug in the
        calling code, and silently correcting the numbers hides it while producing a corpus
        nobody specified. See the module docstring for what each bound prevents.
    """
    if not 0 <= overlap < chunk_size // 2:
        raise ValueError(
            f"overlap must satisfy 0 <= overlap < chunk_size/2; "
            f"got overlap={overlap}, chunk_size={chunk_size}"
        )
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=list(SEPARATORS),
        length_function=len,
    )


def chunk_docs(
    docs: list[Document],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Document]:
    """Split ``docs`` and stamp each chunk with its synthetic id and per-document ordinal.

    Metadata is *added*, never replaced: the splitter already copies each source document's
    metadata onto its chunks, so ``tenant_id`` and any caller-supplied fields survive and land
    in ``doc_chunks.chunk_metadata`` unchanged. Only the three keys this function owns
    (:data:`CHUNK_ID_KEY`, :data:`CHUNK_ORDINAL_KEY`, :data:`DOC_ID_KEY`) are written.

    :param docs: Source documents. Each should carry ``doc_id`` in its metadata; one that does
        not is chunked under :data:`UNKNOWN_DOC_ID` rather than dropped, because a bulk ingest
        should not lose a document over a missing label - the synthetic id makes the gap
        visible downstream instead.
    :param chunk_size: Passed to :func:`make_splitter`.
    :param overlap: Passed to :func:`make_splitter`.
    :returns: The chunks in document order, each ordinal restarting at 0 per ``doc_id``.
    :raises ValueError: propagated from :func:`make_splitter` on an illegal overlap.
    """
    splitter = make_splitter(chunk_size=chunk_size, overlap=overlap)
    chunks = splitter.split_documents(docs)

    # Counted per doc_id rather than with enumerate() over the whole list: the ordinal is a
    # position WITHIN a document, and a global counter would renumber every later document when
    # an earlier one changed length. See the module docstring.
    seen: dict[str, int] = {}
    for chunk in chunks:
        doc_id = str(chunk.metadata.get(DOC_ID_KEY, UNKNOWN_DOC_ID))
        ordinal = seen.get(doc_id, 0)
        seen[doc_id] = ordinal + 1
        chunk.metadata[DOC_ID_KEY] = doc_id
        chunk.metadata[CHUNK_ORDINAL_KEY] = ordinal
        chunk.metadata[CHUNK_ID_KEY] = chunk_id_for(doc_id, ordinal)

    _LOG.info(
        "chunker.split.completed",
        extra={
            "event": "chunker.split.completed",
            "documents": len(docs),
            "chunks": len(chunks),
            "chunk_size": chunk_size,
            "overlap": overlap,
        },
    )
    return chunks
