# taxcalc-mcp-server/src/taxcalc_mcp_server/tools/_resources.py
"""The one read-only resource: ``taxcalc://catalogue``.

**Why publish a catalogue when ``tools/list`` already exists.** They answer different questions.
``tools/list`` is the protocol's own inventory: names, schemas, descriptions - everything a
client needs to *call* a tool. The catalogue is a description of the *surface*: which tools are
reads and which are writes, which one is idempotent and on what key, and how much corpus is
behind the retrieval tool - how many chunks, split across which tenants. An agent planning a
multi-step task uses that to decide the shape of its plan before it commits to a call - it is the
difference between "what can I invoke" and "what am I working with".

It is also a fallback. The W7 D5 agent reads this at startup, and a resource read is one request
that survives a client whose ``tools/list`` cache is stale, or a transport that negotiated a
protocol version where the tool list arrives later than the agent's first decision.

**Resources are reads, and this one is a pure function of static configuration.** No database
call, no HTTP hop, no credential. That is deliberate: a resource an agent polls at startup must
not be a way to make this server do expensive work on someone else's behalf.
"""

from __future__ import annotations

from typing import Final

from taxcalc_mcp_server import __version__
from taxcalc_mcp_server.app import mcp

#: URI the catalogue is published at. A custom scheme rather than ``file://`` or ``https://``
#: because the content is this server's own view of itself and is not fetchable any other way -
#: naming it with a borrowed scheme would imply a second, dereferenceable location that does not
#: exist. Committed into ``mcp.json`` under ``resources``, so it is a contract, not a label.
CATALOGUE_URI: Final[str] = "taxcalc://catalogue"

#: Per-tool summary. ``write`` is the field an agent's approval gate reads: the W7 D5 HITL node
#: pauses on a tool that moves money and waves a read through, and it needs that fact *before*
#: it calls the tool, which is exactly what a catalogue is for.
_TOOLS: Final[list[dict[str, object]]] = [
    {
        "name": "orders.get_order",
        "write": False,
        "idempotent": True,
        "summary": "Read one order's total and status for a tenant.",
    },
    {
        "name": "orders.create_refund",
        "write": True,
        "idempotent": True,
        "idempotency_key": "idempotency_key (UUID v4)",
        "summary": "Refund an order. Safe to retry with the same key; never double-debits.",
    },
    {
        "name": "llm.chat",
        "write": False,
        "idempotent": False,
        "summary": "Ungrounded chat through the cost-tracked LLM proxy.",
    },
    {
        "name": "rag.retrieve_and_generate",
        "write": False,
        "idempotent": False,
        "summary": "Answer from the tenant's document corpus, with citations and coverage.",
    },
]

#: Size of the seeded corpus, as chunk counts per tenant. Declared as a literal and asserted
#: against the seed fixture by ``tests/test_resources.py``, which is what keeps it honest: the
#: numbers are checked against ``taxcalc-ai/tests/fixtures/corpus_seed.jsonl`` on every test run,
#: so a re-seed that changes the corpus fails the build here rather than quietly publishing a
#: stale figure to every agent that reads the catalogue.
#:
#: Static rather than counted live, for the same reason the rest of :data:`_CORPUS` is: a
#: ``SELECT count(*)`` behind an unauthenticated resource read is a way to make this server do
#: database work on an anonymous caller's behalf, and the answer is stale the moment it is
#: returned anyway. What an agent needs from a size is an order of magnitude - "is this corpus
#: 100 chunks or 10 million" decides whether a broad question is answerable by retrieval at all
#: - and that does not change between re-seeds.
_CORPUS_CHUNKS_PER_TENANT: Final[dict[str, int]] = {
    "tenant-a": 36,
    "tenant-b": 33,
    "tenant-c": 31,
}

#: Short corpus stats. Static because they describe the *shape* of the corpus the retrieval tool
#: searches - which tenants exist, how large it is, roughly how it is chunked - not its live row
#: count. A live count would put a database round-trip behind an unauthenticated resource read,
#: and would give an agent a number that is stale the moment it is read anyway.
_CORPUS: Final[dict[str, object]] = {
    "tenants": list(_CORPUS_CHUNKS_PER_TENANT),
    "size": {
        "chunks": sum(_CORPUS_CHUNKS_PER_TENANT.values()),
        # One chunk per document in this corpus: the seed documents are single-passage tax
        # guidance notes, short enough that the W7 D3 chunker emits one chunk each. Published as
        # two separate fields rather than one anyway, because they are two different facts and a
        # corpus re-seeded with longer documents would make them differ.
        "documents": sum(_CORPUS_CHUNKS_PER_TENANT.values()),
        "chunks_per_tenant": dict(_CORPUS_CHUNKS_PER_TENANT),
    },
    "retrieval": "hybrid dense+sparse, RRF fused, MMR diversified, cross-encoder reranked",
    "default_top_k": 6,
    "max_top_k": 20,
}


#: What a client sees next to the resource in ``resources/list``.
#:
#: Passed explicitly because FastMCP falls back to the handler's ``__doc__`` otherwise - and this
#: project's docstrings are reST, so the fallback publishes ``:returns:`` markup and internal
#: rationale to every client that lists resources. The MCP Inspector showed exactly that. Tools
#: never had the problem because each one passes an explicit ``description``; the resource was the
#: one place the default was left to apply.
_DESC_CATALOGUE: Final[str] = (
    "The tool catalogue and corpus summary for this server: each tool's name, whether it writes, "
    "whether it is idempotent and on which key, plus the tenants and retrieval shape behind "
    "rag.retrieve_and_generate. Read this at startup to plan which tools a task needs - "
    "in particular to decide which calls require an approval step, which tools/list does not say."
)


@mcp.resource(
    uri=CATALOGUE_URI,
    name="catalogue",
    description=_DESC_CATALOGUE,
    mime_type="application/json",
)
def catalogue() -> dict[str, object]:
    """Return the tool catalogue and corpus summary.

    :returns: ``server``, ``version``, ``tools`` and ``corpus``. A dict rather than a rendered
        JSON string: the SDK serialises it, so there is one encoder rather than two, and the
        resource cannot drift into emitting JSON this server's own tools would reject.
    """
    return {
        "server": "taxcalc-mcp-server",
        "version": __version__,
        "tools": _TOOLS,
        "corpus": _CORPUS,
    }
