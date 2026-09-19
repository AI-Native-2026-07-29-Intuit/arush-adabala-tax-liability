# taxcalc-agent-svc/src/taxcalc_agent_svc/retrievers.py
"""The injection seam between this service and the W7 D3 retrieval pipeline.

One function, deliberately narrow: ``(query, tenant_id, top_k) -> raw result``. Everything the
pipeline needs that is *not* part of the question - the Anthropic client, the corpus connection,
the Redis handle, the 80 MB cross-encoder - is resolved in here and nowhere else.

**Why that narrowness is the whole point.** When these dependencies are reached for inside the
node body, a test of the retrieval node needs a live Postgres, a live Redis and an API key to
assert something as simple as "a timeout lands an empty ``docs`` slot". With the seam here, a
test substitutes this one callable and needs none of them. The W7 D4 server learned the same
lesson the same way - its ``rag_entrypoint`` exists for exactly this reason - and the shape is
copied on purpose rather than re-derived.

**Clients are opened once and cached for the life of the process.** A per-call connection would
spend a TCP handshake and a TLS negotiation on every question asked. The cache is module-level
because the pipeline's clients are genuinely process-wide resources; the things that are *per
request* - the budget guard, the MCP session's identity - are not here.

**Synchronous on purpose.** The pipeline is synchronous, and its only caller runs it through
``asyncio.to_thread``. That is what makes the deferred import safe: the model load happens on a
worker thread rather than on the event loop.
"""

from __future__ import annotations

import os
from typing import Any, Final

import psycopg
import redis
from psycopg.rows import TupleRow

#: Environment variables the W7 D3 pipeline's clients are built from. Shared with the sidecar and
#: the MCP server, which is why they carry the sidecar's prefix rather than this service's.
PG_DSN_ENV: Final[str] = "TAXCALC_AI_PG_DSN"
REDIS_URL_ENV: Final[str] = "TAXCALC_AI_REDIS_URL"

#: Lazily-opened pipeline clients, cached for the life of the process.
_CLIENTS: dict[str, Any] = {}


def _clients() -> tuple[Any, psycopg.Connection[TupleRow], redis.Redis]:
    """Open (once) and return the three clients the pipeline is injected with.

    :returns: The Anthropic client, the corpus connection, and the Redis client.
    :raises KeyError: if :data:`PG_DSN_ENV` or :data:`REDIS_URL_ENV` is unset. Deliberately not
        defaulted to a localhost DSN: a default turns a misconfigured deployment into a process
        that connects somewhere plausible and wrong, which is discovered much later than one that
        refuses.
    """
    if not _CLIENTS:
        from anthropic import Anthropic

        _CLIENTS["anthropic"] = Anthropic()
        _CLIENTS["conn"] = psycopg.connect(os.environ[PG_DSN_ENV])
        _CLIENTS["redis"] = redis.from_url(os.environ[REDIS_URL_ENV])
    conn = _CLIENTS["conn"]
    r = _CLIENTS["redis"]
    assert isinstance(conn, psycopg.Connection)  # noqa: S101 - narrowing a heterogeneous cache
    assert isinstance(r, redis.Redis)  # noqa: S101
    return _CLIENTS["anthropic"], conn, r


def run_pipeline(query: str, tenant_id: str, top_k: int) -> dict[str, Any]:
    """Run the W7 D3 pipeline for one query, opening its clients and its model on first use.

    :param query: The (already rewritten) retrieval query.
    :param tenant_id: Whose corpus to search. A security boundary at three layers inside the
        pipeline: the SQL pre-filter, the cache key, and the citation check in the cache lookup.
    :param top_k: Retained for symmetry with the caller's arguments; the pipeline's own stage
        sizes govern retrieval width and the caller truncates afterwards.
    :returns: The pipeline's raw result - ``text``, ``citations``, ``rerank_timed_out`` and
        ``coverage``.
    """
    from taxcalc_ai.rag import retrieve_and_generate

    anthropic, conn, r = _clients()
    del top_k  # named for the caller's benefit; see the parameter docs
    return retrieve_and_generate(query, tenant_id, anthropic=anthropic, conn=conn, r=r)
