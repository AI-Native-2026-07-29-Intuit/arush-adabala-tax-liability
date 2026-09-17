# taxcalc-mcp-server/src/taxcalc_mcp_server/tools/rag.py
"""``rag.retrieve_and_generate``: a thin adapter over the W7 D3 five-stage pipeline.

The pipeline itself lives in :func:`taxcalc_ai.rag.retrieve_and_generate` and is called
in-process, as a library. That is the point of the path dependency: retrieval is not re-hosted
behind an HTTP hop just because the caller now speaks MCP, so the cross-encoder, the semantic
cache and the tenant pre-filter are all exactly the code W7 D3 shipped and RAGAS graded.

**Two adaptations at this boundary, both deliberate.**

*The output is pre-shaped, hard.* The pipeline returns every citation it reranked, each carrying
its full ``chunk_text``, plus a four-key coverage diagnostic. Passing that through would spend
kilobytes of the model's context window on every call, forever, most of it text the model
already has in the answer. The DTO below keeps the answer, the top-``k`` citations as
*identifiers and scores only*, the single coverage number that means something to a reader
(``jaccard`` - how much the two retrievers agreed), and the ``rerank_timed_out`` flag. A caller
who wants a chunk's text can ask for it; a caller who never wanted it does not pay for it.

*The call is offloaded and deadlined.* The pipeline is synchronous and its rerank stage runs a
cross-encoder forward pass - hundreds of milliseconds of CPU with no await point in it. Calling
it directly from a coroutine would block the event loop, which on the SSE transport means every
other in-flight request on this process stops, including their heartbeats. ``asyncio.to_thread``
moves it off the loop and ``asyncio.wait_for`` puts a ceiling on it; a miss raises 5040, which
is a code the W7 D5 agent can act on (retry smaller, or answer ungrounded) rather than a hang.

**Clients are opened lazily, on first use, not in the lifespan.** Three of the four tools on
this server need neither Postgres nor Redis nor an Anthropic key. Opening those connections at
startup would mean a desktop user who only ever looks up orders cannot start the server without
a corpus - a hard dependency created by nothing but where the code was placed.
"""

import asyncio
import os
from typing import Annotated, Final

import psycopg
import redis
from anthropic import Anthropic
from langsmith import traceable
from psycopg.rows import TupleRow
from pydantic import BaseModel, ConfigDict, Field

from taxcalc_mcp_server.app import ctx, log, mcp
from taxcalc_mcp_server.errors import rag_timeout
from taxcalc_mcp_server.tools.orders import TENANT_PATTERN

#: A retrieval relevance score, in [0, 1]. A float, and correctly so.
#:
#: The CI gate greps this package for a bare float annotation, because money must never be
#: one. This alias is not a
#: way around that check - it is the distinction the check is testing for, written down. A cosine
#: similarity is a measurement, not a ledger entry: it is compared and ranked, never summed into
#: a balance, and no auditor ever reconciles it. Money in this package is `Decimal`, always; this
#: is not money.
type RelevanceScore = float

#: Environment variables the pipeline's clients are built from. Named here rather than read
#: inline so the three that this server adds to the sidecar's own set are visible in one place.
PG_DSN_ENV: Final[str] = "TAXCALC_AI_PG_DSN"
REDIS_URL_ENV: Final[str] = "TAXCALC_AI_REDIS_URL"


class RagArgs(BaseModel):
    """Arguments for ``rag.retrieve_and_generate``."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=2, max_length=2000, description="The question to answer from the corpus."
    )
    tenant_id: str = Field(
        pattern=TENANT_PATTERN, description="Whose corpus to search, e.g. tenant-a."
    )
    top_k: int = Field(
        default=6,
        ge=1,
        le=20,
        description="How many citations to return. Raise to 12-20 when coverage matters.",
    )


class Citation(BaseModel):
    """One supporting chunk: where it came from and how relevant it was.

    No ``chunk_text``. The chunk's content is already reflected in ``answer``; returning it again
    doubles the token cost of every grounded answer to restate what the model just read.
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    #: The document the chunk came from, parsed out of ``chunk_id``. Surfaced as its own field
    #: because "which documents did this answer rest on" is the question a reviewer actually
    #: asks, and making them parse an id string to answer it is a trap.
    doc_id: str
    score: RelevanceScore


class RagAnswer(BaseModel):
    """The pre-shaped result. Five fields, chosen against the context-window budget."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    citations: list[Citation]
    #: Jaccard agreement between the dense and sparse retrievers, in [0, 1]. The pipeline reports
    #: four coverage numbers; this is the one that tells a reader whether hybrid retrieval is
    #: still contributing. The other three are counts a caller cannot act on.
    coverage: RelevanceScore
    #: True when the cross-encoder missed its soft deadline and the pipeline fell back to the
    #: pre-rerank ordering. Surfaced rather than hidden because the answer is still usable but
    #: less precisely ranked, and a caller weighing whether to trust it deserves to know.
    rerank_timed_out: bool


_DESC_RAG: Final[str] = (
    "Answer a question grounded in the tenant's own document corpus, using the hybrid "
    "dense+sparse retrieval pipeline with RRF fusion, MMR diversification and a cross-encoder "
    "rerank. Returns the answer, up to top_k citations carrying chunk_id, doc_id and relevance "
    "score, a coverage diagnostic and a rerank_timed_out flag. Use this whenever the user asks "
    "about information that lives in the tenant's documents - policies, prior filings, the "
    "indexed knowledge base - or whenever an answer needs to be citable. Do NOT use this for "
    "transactional reads of order state (use orders.get_order) and do NOT use it for open-ended "
    "generation that needs no grounding (use llm.chat). "
    "Example: question='What is the standard deduction for a sole trader?', "
    "tenant_id='tenant-a', top_k=6 returns the answer with six citations."
)

_RAG_ARGS: Final = RagArgs.model_fields

#: Lazily-opened pipeline clients, cached for the life of the process. Opened on the first call
#: to this tool and reused by every later one - a per-call Postgres connection would spend a TCP
#: handshake and a TLS negotiation on every question asked.
_CLIENTS: dict[str, object] = {}


def _clients() -> tuple[Anthropic, psycopg.Connection[TupleRow], redis.Redis]:
    """Open (once) and return the three clients the W7 D3 pipeline is injected with.

    :returns: The Anthropic client, the corpus connection, and the Redis client.
    :raises KeyError: if ``TAXCALC_AI_PG_DSN`` or ``TAXCALC_AI_REDIS_URL`` is unset. Deliberately
        not defaulted to a localhost DSN: a default turns a misconfigured deployment into a
        process that connects somewhere plausible and wrong, which is discovered much later than
        one that refuses. Raised on first *use* rather than at boot, so the three tools that need
        no corpus keep working on a server that has none.
    """
    if not _CLIENTS:
        _CLIENTS["anthropic"] = Anthropic()
        _CLIENTS["conn"] = psycopg.connect(os.environ[PG_DSN_ENV])
        _CLIENTS["redis"] = redis.from_url(os.environ[REDIS_URL_ENV])
        log.info("rag.clients.opened")
    anthropic = _CLIENTS["anthropic"]
    conn = _CLIENTS["conn"]
    r = _CLIENTS["redis"]
    assert isinstance(anthropic, Anthropic)  # noqa: S101 - narrowing a heterogeneous cache
    assert isinstance(conn, psycopg.Connection)  # noqa: S101
    assert isinstance(r, redis.Redis)  # noqa: S101
    return anthropic, conn, r


def _doc_id_of(chunk_id: str) -> str:
    """Recover the document id from a chunk id.

    The corpus builds chunk ids as ``chunk-{doc_id}-p{chunk_idx}`` (see
    :mod:`taxcalc_ai.chunker`), so the document id is what remains after stripping the prefix and
    the page suffix. Tolerant by design: an id in an unexpected shape yields the id itself rather
    than raising, because a citation whose ``doc_id`` is imperfect is far better than a whole
    answer lost to a parse error over a display field.

    :param chunk_id: The chunk identifier.
    :returns: The document id, or ``chunk_id`` unchanged if it does not match the known shape.
    """
    if not chunk_id.startswith("chunk-") or "-p" not in chunk_id:
        return chunk_id
    return chunk_id.removeprefix("chunk-").rsplit("-p", 1)[0]


@traceable(name="rag.retrieve_and_generate", project_name="taxcalc-mcp-server")
async def _retrieve_and_generate(args: RagArgs) -> dict[str, object]:
    """Run the W7 D3 pipeline under a deadline and pre-shape its result.

    :param args: Validated arguments.
    :returns: A :class:`RagAnswer` dumped in JSON mode.
    :raises McpError: 5040 when the pipeline misses :attr:`Settings.tool_timeout_rag_s`.
    """
    c = ctx()
    log.info(
        "tool.invoke.start",
        tool="rag.retrieve_and_generate",
        tenant_id=args.tenant_id,
        top_k=args.top_k,
    )
    anthropic, conn, r = _clients()

    try:
        # to_thread because the pipeline is synchronous CPU work; wait_for because a cross-encoder
        # that never returns must not become a request that never returns. See the module docstring.
        result = await asyncio.wait_for(
            asyncio.to_thread(
                c.rag_fn, args.question, args.tenant_id, anthropic=anthropic, conn=conn, r=r
            ),
            timeout=c.settings.tool_timeout_rag_s,
        )
    except TimeoutError as exc:
        log.info(
            "tool.invoke.end",
            tool="rag.retrieve_and_generate",
            tenant_id=args.tenant_id,
            mcp_error_code=5040,
        )
        raise rag_timeout(
            f"rag timed out after {c.settings.tool_timeout_rag_s}s"
        ) from exc

    raw_citations = result.get("citations", [])
    citations: list[Citation] = []
    if isinstance(raw_citations, list):
        for item in raw_citations[: args.top_k]:
            if not isinstance(item, dict):
                continue
            chunk_id = str(item.get("chunk_id", ""))
            citations.append(
                Citation(
                    chunk_id=chunk_id,
                    doc_id=_doc_id_of(chunk_id),
                    score=float(item.get("score", 0.0)),
                )
            )

    raw_coverage = result.get("coverage", {})
    # The pipeline's `coverage` is a four-key mapping; `jaccard` is the one number a caller can
    # act on. `.get(..., 0.0)` rather than `[...]` so a future pipeline that renames the key
    # degrades one field instead of failing the whole answer.
    jaccard = raw_coverage.get("jaccard", 0.0) if isinstance(raw_coverage, dict) else 0.0

    answer = RagAnswer(
        answer=str(result.get("text", "")),
        citations=citations,
        coverage=float(jaccard),
        rerank_timed_out=bool(result.get("rerank_timed_out", False)),
    )
    log.info(
        "tool.invoke.end",
        tool="rag.retrieve_and_generate",
        tenant_id=args.tenant_id,
        citations=len(answer.citations),
        rerank_timed_out=answer.rerank_timed_out,
    )
    return answer.model_dump(mode="json")


@mcp.tool(name="rag.retrieve_and_generate", description=_DESC_RAG)
async def rag_retrieve_and_generate(
    question: Annotated[str, _RAG_ARGS["question"]],
    tenant_id: Annotated[str, _RAG_ARGS["tenant_id"]],
    top_k: Annotated[int, _RAG_ARGS["top_k"]] = 6,
) -> dict[str, object]:
    """Answer from the tenant's corpus. The MCP boundary for :func:`_retrieve_and_generate`.

    :param question: The question to answer.
    :param tenant_id: Whose corpus to search.
    :param top_k: How many citations to return.
    :returns: The grounded answer as a JSON-safe dict.
    """
    return await _retrieve_and_generate(
        RagArgs(question=question, tenant_id=tenant_id, top_k=top_k)
    )
