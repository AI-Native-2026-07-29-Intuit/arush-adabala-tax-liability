# taxcalc-mcp-server

One MCP server publishing the capstone's order, chat and retrieval surfaces as **four tools and
one resource**, over **two transports** — stdio for Claude Desktop, HTTP+SSE for the W7 D5
multi-agent orchestrator.

```
                   ┌──────────────────────┐
Claude Desktop ────┤ stdio                │
                   │   taxcalc-mcp-server │──── orders.*  ──→ taxcalc-orders   (Java, this repo)
W7 D5 agent    ────┤ HTTP+SSE             │──── llm.chat  ──→ llm-proxy        (Java, this repo)
                   └──────────────────────┘──── rag.*     ──→ taxcalc_ai.rag   (Python, in-process)
```

**This server owns no business logic.** Every tool validates its arguments, forwards to something
that already exists, and re-shapes the reply. What it adds is the *contract* — transports,
schemas, error codes, idempotency, tracing — which downstream LLM clients code against.

New here? Read this file, then [Where the reasoning lives](#where-the-reasoning-lives) for the
*why* behind any decision that looks odd.

---

## The four tools

| Tool | Signature | Writes? | Notes |
|---|---|---|---|
| `orders.get_order` | `(order_id, tenant_id)` | no | Reads one order. 4040 when it does not exist *for that tenant*. |
| `orders.create_refund` | `(order_id, amount, reason, tenant_id, idempotency_key)` | **yes** | Idempotent on a UUID v4 key. Retrying never double-debits. |
| `llm.chat` | `(messages, max_tokens, tenant_id)` | no | Ungrounded chat through the cost-tracked proxy. 4290 on a rate limit. |
| `rag.retrieve_and_generate` | `(question, tenant_id, top_k=6)` | no | Grounded answer with citations, via the W7 D3 pipeline in-process. |

Plus one read-only resource, **`taxcalc://catalogue`** — the tool catalogue and corpus shape. It
answers a different question from `tools/list`: not *"what can I invoke"* but *"what am I working
with"*, including which tools **write** and which are idempotent on what key. That is what an
agent's approval gate needs *before* it calls anything.

### Three rules every tool obeys

**Money is `Decimal`, and it travels as a string.** `Decimal(0.1)` is
`0.1000000000000000055511151231257827` — by the time a float reaches a validator the exact value
is gone. A float `amount` is **rejected**, not coerced. `10.001` is **rejected, not rounded**:
rounding would refund a different amount than the caller asked for and tell nobody. On the wire
it is `"10.00"`, so the Java `BigDecimal` reads the scale too. See
[`numeric.py`](src/taxcalc_mcp_server/numeric.py).

**Unknown arguments are refused, and the schema says so.** Every input model sets
`extra="forbid"` *and* the published JSON Schema carries `additionalProperties: false`. Both
halves matter: advertising a rule you do not enforce invites clients to trust a check that is not
happening.

**Errors carry numeric codes, not prose.** One table,
[`errors.py`](src/taxcalc_mcp_server/errors.py), is the single source of truth:

| Upstream | Code | | Upstream | Code |
|---|---|---|---|---|
| 400 | `4001` | | 429 | `4290` |
| 401 / 403 | `4030` | | 5xx / unmapped | `5030` |
| 404 | `4040` | | RAG timeout | `5040` |
| 409 | `4090` | | bad arguments | `-32602` |

A caller branches on the number. `4290` means back off and retry; `4030` means stop, retrying
will not help. Folded into one generic code those two are indistinguishable, and the correct
response to them is opposite.

---

## taxcalc-orders — the service the `orders.*` tools call

[`../taxcalc-orders/`](../taxcalc-orders/) is a real Spring Boot service in this repo: Postgres,
Flyway, JdbcTemplate, no JPA. It owns the refund ledger.

| Endpoint | Purpose |
|---|---|
| `GET /orders/{orderId}` | Read one order for the calling tenant |
| `POST /orders/{orderId}/refunds` | Issue a refund, absorbing retries |
| `GET /orders/{orderId}/refunds?idempotency_key=…` | Count rows for a key — how the guarantee is checked from outside |
| `GET /actuator/health` | Readiness; the E2E polls it |

Every request needs `Authorization: Bearer …` and `X-Tenant`. It seeds one order:
**`ord-synth-9001`**, tenant `tenant-a`, total `42.50`, status `paid` — the same row the fixtures,
the stdio smoke test and the E2E all reference.

**The idempotency guarantee lives in a unique index on `(tenant_id, idempotency_key)`, not in
application code.** "Look the key up, insert if absent" reads correctly and is wrong: two retries
of one request arrive concurrently as a matter of course — a retry is what a caller does when the
first response was slow — so both lookups find nothing, both inserts succeed, the ledger is
debited twice, and no line of code misbehaved. The database is the only participant that sees
both statements. The key is tenant-scoped, because a global key space lets one tenant's UUID
silently suppress another's legitimate refund.

> **Why this service exists.** The course's `uptimecrew/taxcalc-orders:w3d1` image is not
> pullable, and this capstone's Java service is a *taxpayer* service with no order domain. Without
> a real one the E2E skips, and the assertion that matters most — that a retried refund debits
> once — gets checked only against a stub this repo wrote, which proves nothing.
>
> ⚠️ **`TenantAuthFilter` authenticates by presence, not signature.** It requires a bearer token
> and `X-Tenant`; it does not verify signature, issuer, audience or scopes, because that needs an
> identity provider in the test topology. Presence is exactly what the E2E asserts and what
> regresses. **Do not deploy it as a trust boundary** — the service that owns the data verifies
> the token against the real issuer.

---

## Run it

```bash
cd taxcalc-mcp-server
uv sync --frozen
```

Behind a TLS-intercepting proxy, uv needs the system trust store: `export UV_SYSTEM_CERTS=1`.
Deliberately not baked into `pyproject.toml` — runners do not need it, and a config that always
trusts the system store hides a real certificate problem.

### Configuration

All of it is `TAXCALC_MCP_`-prefixed and validated at boot. Copy [`.env.example`](.env.example);
never commit `.env`.

| Variable | Default | |
|---|---|---|
| `TAXCALC_MCP_ORDERS_SVC_URL` | `https://taxcalc-orders.internal` | |
| `TAXCALC_MCP_LLM_PROXY_URL` | `https://llm-proxy.internal` | |
| `TAXCALC_MCP_LLM_PROXY_CHAT_PATH` | `/v1/completions` | selects the upstream wire shape |
| `TAXCALC_MCP_BEARER_JWT` | *(empty)* | forwarded to both services — **secret** |
| `TAXCALC_MCP_TOOL_TIMEOUT_DEFAULT_S` | `5` | the two HTTP tools |
| `TAXCALC_MCP_TOOL_TIMEOUT_RAG_S` | `30` | retrieval runs a cross-encoder |
| `TAXCALC_MCP_JWKS_URL` | *(empty)* | opt-in local bearer validation |
| `TAXCALC_MCP_HOST` / `_PORT` | `0.0.0.0` / `8080` | SSE bind |
| `TAXCALC_MCP_LANGSMITH_PROJECT` | `taxcalc-mcp-server` | **note the prefix** — bare `LANGSMITH_PROJECT` does *not* move tool spans |

`LANGSMITH_API_KEY` is also required, because `taxcalc_ai.rag` raises at import without one — so
the RAG tool is the one that fails without it; the other three work regardless.

### stdio (Claude Desktop)

```bash
uv run taxcalc-mcp-server
```

Drop [`configs/claude_desktop_config.json`](configs/claude_desktop_config.json) into
`~/Library/Application Support/Claude/` and replace the placeholders.

> **The launch command is `uv run --directory`, not `uvx taxcalc-mcp-server`**, and that is not a
> style choice. This project depends on `taxcalc-ai`, a sibling published to no registry. uv
> resolves it through `[tool.uv.sources]` while working *inside* the project, but the built wheel
> carries a bare `Requires-Dist: taxcalc-ai`, so `uvx` and `pipx install ./dist/*.whl` both fail
> with *"taxcalc-ai was not found in the package registry"*. To install the wheel anyway, put the
> sibling's wheel on the resolver path:
>
> ```bash
> uv build && (cd ../taxcalc-ai && uv build)
> pipx install ./dist/taxcalc_mcp_server-0.1.0-py3-none-any.whl \
>   --pip-args="--find-links $(cd ../taxcalc-ai/dist && pwd)"
> ```

**Nothing may write to stdout.** On stdio, stdout *is* the protocol — one stray byte corrupts the
frame a client is mid-parse of and kills the session. Both logging paths are pinned to stderr and
`ruff`'s `T20` bans `print` package-wide.

### HTTP+SSE (the W7 D5 agent)

```bash
TAXCALC_MCP_HOST=127.0.0.1 TAXCALC_MCP_PORT=8080 uv run taxcalc-mcp-server-sse
```

```bash
curl -s http://127.0.0.1:8080/sse                                     # {"code":4030,...}  HTTP 401
curl -sN -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/sse  # event: endpoint
```

**That 401 is the healthy response.** It proves the transport is up *and* the bearer middleware
is in front of it, which is why [`healthcheck.py`](src/taxcalc_mcp_server/scripts/healthcheck.py)
treats it as healthy — requiring a 2xx would mean baking a real JWT into the image.

### Drive it by hand

```bash
uv run python scripts/inspector_session.py     # MCP Inspector, as a diffable transcript
```

---

## Test it

```bash
uv run ruff check
uv run mypy --strict src/ tests/ scripts/
uv run pytest -v -m "not e2e" --cov=src --cov-fail-under=70   # the PR tier
uv run pytest -v -m e2e                                        # the merge tier
```

The E2E is excluded by **marker, not filename**, so a mis-marked test fails loudly on the merge
tier rather than quietly never running.

| Suite | What it actually proves |
|---|---|
| [`test_schemas.py`](tests/test_schemas.py) | Strict schemas, exact money, every error-table row round-trips |
| [`test_tool_descriptions.py`](tests/test_tool_descriptions.py) | Descriptions pass the routing gate; `mcp.json` has not drifted; **the committed launch command actually starts the server** |
| [`test_smoke_stdio.py`](tests/test_smoke_stdio.py) | 100 request/response pairs against a real subprocess — stdout stays clean |
| [`test_transport_and_scripts.py`](tests/test_transport_and_scripts.py) | SSE bearer middleware, tool error paths, the operator scripts |
| [`test_tracing.py`](tests/test_tracing.py) | Each tool emits exactly one run, named after itself, into the configured project |
| [`test_numeric.py`](tests/test_numeric.py) · [`test_llm_wire_shapes.py`](tests/test_llm_wire_shapes.py) · [`test_resources.py`](tests/test_resources.py) | Money discipline, both proxy shapes, the catalogue |
| [`test_e2e_mcp_to_spring.py`](tests/test_e2e_mcp_to_spring.py) | Postgres + taxcalc-orders + this server, three real processes |

**The E2E needs Docker and a JDK** and builds the order-service jar itself if missing. It **skips
naming the exact cause** when it cannot run — and CI treats a skip on the merge tier as a failure,
because a gate that reports an absence as a pass is worse than no gate.

Why a *description* gate: a tool that raises gets an error someone can act on. A tool whose
description does not say *when* to use it simply never gets called — the model picks something
else, answers worse, and nothing logs a problem. There is no stack trace for "the model did not
consider this tool".

### Latency

```bash
uv run python -m taxcalc_mcp_server.scripts.replay --fixtures tests/fixtures/
```

Times **this server's own work** against canned upstreams, deliberately excluding the network: a
change here cannot make the network faster, and a gate that fires on other teams' deploys stops
being read. It compares p95 as a **ratio against the previous run**, because an absolute
millisecond budget is a statement about the CI runner, not about the diff.

### Tracing actually delivers

```bash
LANGSMITH_API_KEY=… LANGSMITH_TRACING=true \
  uv run python -m taxcalc_mcp_server.scripts.assert_spans_visible
```

`test_tracing.py` proves the decorator is wired, offline, on every run. It cannot prove a trace
ever *left* the process — a key scoped to another workspace, a misspelled project, an exit before
the uploader flushes all leave it passing. This fires one call and asks LangSmith whether it can
see the span. It **skips by name** without a key.

### The order service

```bash
cd .. && ./gradlew -p taxcalc-orders test      # 13 JUnit 5 tests, no containers
./gradlew -p taxcalc-orders bootJar            # the artefact the E2E's image copies in
```

---

## Layout

```
src/taxcalc_mcp_server/
├── app.py              FastMCP, lifespan, one shared httpx client, stderr logging,
│                       StructuredErrorFastMCP (see below)
├── settings.py         every env knob, validated at boot
├── errors.py           _map_http — the one HTTP→code table
├── numeric.py          money vs measurement vs count
├── observability.py    one structured span per call, on stderr
├── tenancy.py          per-request bearer + tenant
├── tools/              the four tools and the catalogue resource
├── transports/         stdio.py, sse.py
└── scripts/            replay, healthcheck, assert_spans_visible
```

**`StructuredErrorFastMCP` is the one piece that will look strange.** The MCP SDK swallows a
tool's error code at *two* layers — `Tool.run` wraps every exception into an English `ToolError`,
and the low-level handler turns whatever escapes into an `isError` result. A 404 arrived as
`'Error executing tool orders.get_order: {"error": "order not found"}'` with no `4040` anywhere:
the entire error table, discarded one layer below the code that built it. Both layers had to be
opened. Fixing only the first removes the prefix and changes nothing else.

---

## Where the reasoning lives

This README is the front door. The *why* behind individual decisions is deliberately kept next to
the code it governs:

| For | Read |
|---|---|
| Why a module is shaped the way it is | its own docstring — they carry the reasoning, not a summary |
| What this means for the W7 D3 sidecar | [`../taxcalc-ai/PYTHON.md`](../taxcalc-ai/PYTHON.md) → *What W7 D4 adds* |
| What Claude got wrong and what was changed | [`PROMPT_JOURNAL.md`](PROMPT_JOURNAL.md) |
| Repo-wide conventions | [`../CLAUDE.md`](../CLAUDE.md) |
| CI tiers | [`../.github/workflows/taxcalc_mcp_server-ci.yml`](../.github/workflows/taxcalc_mcp_server-ci.yml) |

## Known limitations

- **`mcp` is pinned `>=1.2,<2`.** 2.x renames `FastMCP` to `MCPServer` and changes the decorator
  and lifespan surfaces. Everything downstream — `mcp.json`, the Desktop launcher, the W7 D5
  agent — is written against the v1 contract. Moving is a rewrite of `app.py` and both transports.
- **`llm.chat` targets `/v1/completions` by default**, this repo's actual proxy route. Both wire
  shapes are supported and selected by `TAXCALC_MCP_LLM_PROXY_CHAT_PATH`; the MCP-facing schema is
  identical either way.
- **The E2E proves our two services agree with each other**, not agreement with the course image.
- **`taxcalc://catalogue`'s corpus stats are static.** A live `SELECT count(*)` behind an
  unauthenticated resource read is a way to make this server do database work for an anonymous
  caller, and the answer is stale the moment it returns.
