# taxcalc-mcp-server/PROMPT_JOURNAL.md

What Claude was asked, what it produced, and what happened to that output before it reached the
tree. The point of keeping this is the third column: the transcripts below are useful mainly
where the generated answer was *plausible and wrong*, because those are the places a reviewer
would otherwise have no reason to look twice.

The pattern across all three is the same. Claude's output was idiomatic for the MCP SDK as it is
usually written about, and the corrections all came from running it rather than reading it.

## W7 D4 transcripts

### 1. Tool descriptions for `orders.create_refund`

**Prompt to Claude:**

> Write a description for an MCP tool `orders.create_refund` that takes
> (order_id, amount Decimal, reason, tenant_id, idempotency_key UUID).
> The description must tell the LLM client WHEN to call this tool, WHEN
> NOT to call it, what scope the caller JWT needs, and one concrete
> example. The tool is idempotent on idempotency_key.

**Raw Claude response (excerpt):**

```
Creates a refund for an order. Accepts the order ID, refund amount,
reason, tenant ID, and an idempotency key to prevent duplicate refunds.
Returns the created refund record.
```

**Used as is / Modified / Rejected:** **Modified.**

Claude wrote a restatement of the function signature. Every clause describes what the parameters
are named, which the schema next to it already says, and not one of them helps a model decide
whether this is the tool to reach for. In particular it has no negative boundary at all — and
the tool it is most likely to be confused with, `orders.get_order`, is the one a model reaches
for when a user asks "did that refund go through?". A model given this description issues a
refund to answer a question about one.

The shipped version adds the three things the prompt asked for and did not get: the `Use this
when the user explicitly asks to refund, credit back, or reverse a charge` trigger, the
`Do NOT use it for partial cancellations, order edits, or to check whether a refund already
happened - call orders.get_order for that` boundary, and a closing worked example. This is also
why `tests/test_tool_descriptions.py` exists: the gap between what was asked for and what came
back was invisible until the description was read against a checklist, and it will be invisible
again next time unless something checks mechanically.

### 2. FastMCP lifespan + shared httpx client + structured logging

**Prompt to Claude:**

> Write the FastMCP entry point for an MCP server. Use an
> @asynccontextmanager lifespan that opens one shared HTTP client for
> the downstream services and closes it on shutdown. Configure structured
> JSON logging. The server runs over stdio.

**Raw Claude response (excerpt):**

```python
mcp = FastMCP(name="taxcalc-mcp-server", version="0.1.0", lifespan=lifespan)

@asynccontextmanager
async def lifespan(_: FastMCP):
    client = httpx.Client(base_url=settings.orders_svc_url)
    logging.basicConfig(level=logging.INFO)
    yield AppCtx(http=client, settings=settings)
    client.close()
```

**Used as is / Modified / Rejected:** **Modified**, in four places, three of which were defects
rather than preferences.

- `httpx.Client` is the **synchronous** client, inside an async lifespan, awaited by async
  handlers. It would have blocked the event loop on every outbound call. Changed to
  `AsyncClient`.
- `logging.basicConfig(level=logging.INFO)` defaults to **stderr** in the standard library, so
  that line happens to be safe — but the structlog configuration Claude wrote alongside it
  defaulted to stdout, and on the stdio transport stdout carries the JSON-RPC frames. One log
  line lands in the middle of a frame and the session dies. Both paths are now pinned to stderr
  explicitly, which is also why `T20` (no `print`) is switched on for the whole package.
- The `yield` is not in a `try`/`finally`, so an exception during shutdown skips `close()` and
  leaks the connection pool.
- `version="0.1.0"` is not a parameter of `FastMCP.__init__` in mcp 1.30 and raises `TypeError`.
  This one is interesting because it is not a hallucination: it was correct in an earlier 1.x,
  which is exactly the kind of error that survives review by looking familiar. The version now
  goes onto the low-level server directly, with a comment saying why the obvious line does not
  work.

### 3. Testcontainers E2E + idempotent refund assertion

**Prompt to Claude:**

> Write a Testcontainers test that starts Postgres and a Spring Boot
> order service, runs my MCP server as a subprocess, and asserts that
> tools/call for orders.create_refund invoked twice with the same
> idempotency_key returns the same refund_id and debits the ledger once.

**Raw Claude response (excerpt):**

```python
def test_create_refund_is_idempotent(mcp_server):
    args = {"order_id": "ord-synth-9001", "amount": 10.00, ...}
    first = _rpc(mcp_server, "tools/call", {...})
    second = _rpc(mcp_server, "tools/call", {...})
    assert first["result"] == second["result"]
```

**Used as is / Modified / Rejected:** **Modified**, and the change is the whole value of the test.

Two problems. `"amount": 10.00` is a **float literal** in the very test that is supposed to
defend the money discipline — it would have been serialised as a JSON number, and the assertion
would have passed while proving the opposite of what it claims. The shipped version sends
`"10.00"` as a string and asserts the scale survives the `BigDecimal` round-trip.

More importantly, `assert first["result"] == second["result"]` is **not an idempotency test**. It
passes if the service issues two separate refunds and happens to render them identically; it
would also pass against a stub that returns a constant. What makes a retry safe is that the
*ledger* was debited once, so the shipped assertion checks the refund ids match **and** that the
ledger holds exactly one row.

The prompt also assumed an order service that could be pulled. It could not
(`uptimecrew/taxcalc-orders:w3d1` returns `pull access denied` here), so `taxcalc-orders/` is a
real implementation of that contract — and writing it moved the idempotency guarantee to where it
belongs. Claude's first draft of the Java side did the natural thing:

```java
if (refunds.findByIdempotencyKey(tenantId, key).isEmpty()) {
    refunds.insert(...);
}
```

**Rejected.** That is the same defect as the float literal, one layer down: it reads correctly and
is wrong under the only conditions that matter. Two retries of one request arrive concurrently as
a matter of course — a retry is what a caller does when the first response was slow — so both
lookups find nothing, both inserts succeed, and the ledger is debited twice with no line of code
having misbehaved. The shipped version pushes the decision into a unique index on
`(tenant_id, idempotency_key)` and reads back the winner on conflict, because the database is the
only participant that sees both statements.

## What running it caught that reading it did not

Three defects in this deliverable were found by driving the server rather than reviewing the
code, and none of them would have failed a type check, a linter, or a reading:

1. **Error codes never reached the client.** `Tool.run` wraps every handler exception into a
   `ToolError` carrying an English string, and the low-level `CallToolRequest` handler converts
   whatever escapes into an `isError` result. A `404` from the order service arrived as
   `'Error executing tool orders.get_order: {"error": "order not found"}'` with no `4040`
   anywhere — the entire centralised error table, silently discarded one layer below the code
   that built it. Fixing only the first of the two layers removed the prefix and changed nothing
   else, which is how it became clear there were two.

2. **A phantom `config` parameter in every tool's published schema.** Stacking `@mcp.tool` over
   `@traceable` makes FastMCP derive the schema from langsmith's wrapper signature, so each tool
   advertised an argument that does not exist and that a model could try to fill.

3. **The first SSE client paid for a tool it had not called.** The lifespan imported the RAG
   pipeline, so the first connection blocked on an 80 MB model load plus five model-hub retries
   before the local cache was used.

## The MCP Inspector run

The deliverable is exercised with `npx @modelcontextprotocol/inspector`. The Inspector's default
mode is a browser UI, so the evidence it leaves behind is a screenshot or a claim — neither of
which survives a rebase or tells a reviewer what actually came back. This is the same tool in
`--cli` mode (Inspector 2.7.0), which uses the same client library over the same stdio transport
and writes its results to stdout, so the run is a transcript that anyone can regenerate:

```
uv run python scripts/inspector_session.py
```

That script starts `tests.stub_orders` on a fixed port, writes a throwaway session config in the
`mcpServers` shape Claude Desktop uses, and runs four Inspector sessions against
`uv run python -m taxcalc_mcp_server.transports.stdio`. The stub rather than the real Spring
service on purpose: what the Inspector run is evidence *for* is that the MCP surface works —
tools registered, resource readable, a call round-tripping. That the order service itself is
correct is `tests/test_e2e_mcp_to_spring.py`'s job, and it proves it against the real container.

Recorded run, elisions marked `[…]` — the interleaved JSON lines are the server's own structured
logs arriving on **stderr**, which is what the stdout/stderr split in `app.py` exists to produce:

```
$ npx @modelcontextprotocol/inspector --cli --method tools/list
{
  "tools": [
    { "name": "orders.get_order",           "description": "Fetch a single order by id […]" },
    { "name": "orders.create_refund",       […] },
    { "name": "llm.chat",                   […] },
    { "name": "rag.retrieve_and_generate",  […] }
  ]
}
{"transport": "stdio", "tools": 4, "event": "transport.start", "level": "info", …}
{"orders_svc": "http://127.0.0.1:8791", "project": "taxcalc-mcp-server", "event": "lifespan.start", …}

$ npx @modelcontextprotocol/inspector --cli --method resources/list
{
  "resources": [
    {
      "name": "catalogue",
      "uri": "taxcalc://catalogue",
      "mimeType": "application/json"
    }
  ]
}

$ npx @modelcontextprotocol/inspector --cli --method tools/call \
    --tool-name orders.get_order --tool-arg order_id=ord-synth-9001 tenant_id=tenant-a
{
  "content": [
    {
      "type": "text",
      "text": "{\n  \"order_id\": \"ord-synth-9001\",\n  \"tenant_id\": \"tenant-a\",\n  \"total\": \"42.50\",\n  \"status\": \"paid\"\n}"
    }
  ],
  "structuredContent": {
    "order_id": "ord-synth-9001",
    "tenant_id": "tenant-a",
    "total": "42.50",
    "status": "paid"
  },
  "isError": false
}
{"tool": "orders.get_order", "tenant_id": "tenant-a", "event": "tool.invoke.start", …}
HTTP Request: GET http://127.0.0.1:8791/orders/ord-synth-9001 "HTTP/1.0 200 OK"
{"tool": "orders.get_order", "tenant_id": "tenant-a", "duration_ms": 2, "http_status": 200, "event": "tool.invoke.end", …}

$ npx @modelcontextprotocol/inspector --cli --method resources/read --uri taxcalc://catalogue
{
  "contents": [
    {
      "uri": "taxcalc://catalogue",
      "mimeType": "application/json",
      "text": "{ \"server\": \"taxcalc-mcp-server\", \"version\": \"0.1.0\", \"tools\": […],
                \"corpus\": { \"tenants\": [\"tenant-a\", \"tenant-b\", \"tenant-c\"],
                              \"size\": { \"chunks\": 100, \"documents\": 100,
                                          \"chunks_per_tenant\": {\"tenant-a\": 36,
                                                                  \"tenant-b\": 33,
                                                                  \"tenant-c\": 31} },
                              […] }}"
    }
  ]
}
```

Four things this confirms that the unit suite does not, because each of them lives between the
handler and a *real* client rather than inside the process: `tools/list` returns
`orders.get_order` with its schema; `resources/list` returns `taxcalc://catalogue`;
`tools/call` against the synthetic order `ord-synth-9001` returns the seeded payload with the
money still a string; and `total` survives a third-party JSON parser as `"42.50"` rather than
`42.5`.

One detail the transcript records rather than asserts: `"version": "0.1.0"` in the catalogue and
in the `initialize` handshake. `version` is not a `FastMCP` constructor argument in mcp 1.30 —
it lives on the low-level server, which `FastMCP.__init__` does not forward — so left alone this
server would introduce itself to every client as `1.30.0`, the version of the *SDK*, disagreeing
with the `0.1.0` pinned in the committed `mcp.json` on every deploy.
`StructuredErrorFastMCP.__init__` takes the `version` FastMCP will not and sets it, which keeps
the reach-through in the class that owns the object and leaves the construction reading as
`StructuredErrorFastMCP(name=…, version=…, lifespan=…)`.
