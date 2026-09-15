# taxcalc-ai — Python sidecar for taxcalc-api

## What lives here

The Python sidecar that owns the AI/ML half of the taxcalc stack. The Java service
(`taxcalc-api`, at the repo root) keeps the transactional Postgres workload and the
latency-sensitive HTTP surface — the JVM's sweet spot, and where it stays. This sidecar calls
the W3 D1 LLM proxy and returns a typed `LiabilityEstimateResult`.

It ships in the same repo as the Java module on purpose. The two share the `Taxpayer` JSON
contract, and a contract change that spans two languages should be one diff, not two PRs in two
repos that can drift apart between merges.

```
taxcalc-ai/
├── pyproject.toml          # uv-managed; runtime deps separated from dev tooling
├── uv.lock                 # committed; CI runs `uv sync --frozen`
├── sql/
│   └── V001__doc_chunks.sql   # W7 D2 — pgvector DDL; NOT a Flyway migration (see below)
├── src/taxcalc_ai/
│   ├── models.py           # Pydantic v2 boundary models
│   ├── value_types.py      # frozen dataclasses (slots=True) — internal only
│   ├── settings.py         # BaseSettings + SecretStr
│   ├── client.py           # httpx + tenacity + structured JSON logs
│   ├── corpus.py           # W7 D2 — Pandas loader + MiniLM embedding pass
│   ├── pgvector_loader.py  # W7 D2 — psycopg v3, register_vector, ON CONFLICT
│   ├── rag.py              # W7 D2 — @traceable cosine ANN retrieval
│   ├── py.typed            # PEP 561 marker — this package ships its own types
│   ├── scripts/            # operator/CI entrypoints; print() allowed, like cli.py
│   │   └── assert_langsmith_run_visible.py
│   └── cli.py              # the one place print() is allowed
└── tests/
    ├── fixtures/corpus_seed.jsonl        # 100 synthetic chunks, 100 docs, 3 tenants
    ├── golden/taxcalc_golden_50.jsonl    # 50-row RAGAS eval set, 4 failure modes
    └── ...                               # pytest; taxpayer_java.json is a captured response
```

## How to run locally

```sh
cd taxcalc-ai
uv sync                                  # creates .venv from the lockfile
uv run pytest -v --cov=src               # 40 tests, all green, 98% coverage
uv run mypy --strict src/ tests/         # zero errors
uv run ruff check && uv run ruff format --check
uv run python -m taxcalc_ai.cli request.json   # validate a payload at the boundary
```

Behind a TLS-inspecting corporate proxy, `uv` needs the system trust store:
`UV_SYSTEM_CERTS=1 uv sync`. That is a local-network artifact, so it is deliberately not baked
into `pyproject.toml` or the CI workflow — GitHub runners do not need it, and a config that
always trusts the system store is a config that hides a real certificate problem.

Always run through `uv run …`, so the venv-pinned `ruff` / `mypy` / `pytest` are what execute
rather than whatever happens to be on the global `PATH`.

## Boundary contract

* Pydantic v2 models in `src/taxcalc_ai/models.py` use `populate_by_name=True` plus `alias=`, so
  Python code constructs with snake_case while the JSON wire form stays camelCase and matches
  the Java service's Jackson output (`displayName`, `createdAt`, `taxYear`, …).
* Every boundary model is `extra="forbid"` and `frozen=True`. Forbidding extras turns a
  Java-side field rename into a loud `ValidationError` at the boundary rather than a silently
  dropped key that becomes a wrong number three frames later.
* Every collection field is a `tuple`, never a `list`. `frozen=True` on a model holding a list
  is shallow — the model rejects attribute assignment, but `model.tags.append(...)` still works.
* Money is `decimal.Decimal`, never `float`, matching `BigDecimal.setScale(2, HALF_UP)` on the
  Java side. Every money field carries `max_digits=14, decimal_places=2`; an unbounded `Decimal`
  would happily accept a 400-digit value off the wire and carry it into arithmetic.
* The `tenacity` retry policy excludes 4xx `HTTPStatusError` on purpose — client errors are not
  transient and should fail fast. Retrying a 400 spends the rate-limit budget three times to
  collect the same rejection; retrying a 401 can lock an account out.
* The CI workflow lives at the repo root (`.github/workflows/python-ci.yml`) but is path-scoped
  to `taxcalc-ai/**`, so Java-only PRs do not pay the Python tax. See that file's header for why
  this workflow may be path-filtered while `ci.yml` next door may not.

### The round-trip fixture: where it comes from

`tests/fixtures/taxpayer_java.json` holds the **response body of a real
`GET /api/v1/taxpayers/taxpayer-001`** against a locally-running taxcalc-api — not a hand-written
document, and not a serialiser invoked in isolation. To reproduce it:

```bash
MINT_ONLY=1 COUNT=1 scripts/loadtest-token.sh          # RS256 keypair + one token, into .loadtest/
docker compose -f compose.yaml -f compose.override.yaml up -d postgres mongo redis kafka
SPRING_PROFILES_ACTIVE=local,loadtest \
TAXCALC_LOADTEST_JWT_PUBLIC_KEY="file:$PWD/.loadtest/public.pem" \
  ./gradlew bootRun                                     # datastore host/ports per your compose mapping
TOKEN=$(python3 -c "import json;print(json.load(open('.loadtest/tokens.json'))[0])")
curl -s http://localhost:8080/api/v1/taxpayers/taxpayer-001 -H "Authorization: Bearer $TOKEN" \
  > taxcalc-ai/tests/fixtures/taxpayer_java.json
```

The committed file is that captured body re-emitted through `TaxpayerReadModel` after the money
fields gained `@JsonFormat(shape = STRING)` (below). Every value in it came off the wire; only the
money **encoding** changed, and re-running the capture above against the current service
reproduces the committed bytes.

Two things about that run are worth knowing before the output surprises you.

**Every route is authenticated and there is no IdP.** The base profile's `issuer-uri` is a
placeholder, so no real token can be minted against it and every request is a 401. The `loadtest`
profile exists for exactly this: it clears `issuer-uri` and validates against a locally-generated
public key. `scripts/loadtest-token.sh` in `MINT_ONLY=1` mode produces the keypair and a token
carrying `taxpayers.read`/`taxpayers.write` and `tenant: tenant-synth`, with no cluster involved.

**The captured document's `tenantId` reads `tenant-shared`, and that is not a placeholder.** The
response came back through the Postgres fallback in `TaxLiabilityService.findById` (Redis → Mongo
→ Postgres), which rebuilds a projection from the JPA entity and has no request context to take
an owning tenant from, so it stamps the documented default. A document written by the
write-through path instead carries the caller's tenant — `POST /api/v1/taxpayers` with the token
above produces `"tenantId":"tenant-synth"`. Both are real outputs of the same endpoint; the
fixture is the one that also carries a liability, and therefore money.

### Money crosses the wire as a string, and that was a change we made

The fixture shows money as a JSON **string**, with its scale written out:

```json
{"taxableAmount":"120000.00","liabilityAmount":"26400.00"}
```

It did not start that way, and the reason it changed is the more useful half of this section.

Jackson's default for `BigDecimal` is a bare JSON **number**. Pydantic emits a `Decimal` as a
JSON **string**. Those two encodings cannot be reconciled by any setting on either side —
Pydantic has no `ser_json_decimal` knob, and more fundamentally a JSON number's trailing zeros
survive **no** parser: `120000.00` arrives as `Decimal('120000')`, exponent 0, in Python's
`json` and in Pydantic alike. So while Java wrote numbers, the round-trip test could only assert
*value* equality after normalising both sides, which was the strongest true statement available
and weaker than the contract deserved.

**The fix was to remove the seam rather than work around it.** `TaxpayerReadModel`'s two money
fields now carry `@JsonFormat(shape = STRING)`, so both ends write the digits verbatim and the
documents compare directly:

```python
assert json.loads(ours) == json.loads(java_taxpayer_json)
```

No normalisation, no coercion, nothing for a future bug to hide behind. Key ordering is the only
remaining difference, which is what comparing parsed documents rather than raw bytes accounts
for.

**This was not only about the Python test.** JavaScript has one numeric type, IEEE-754 double,
so `JSON.parse` turned `120000.00` into a float before any React code saw it. Money on a wire
that a JS client reads is the textbook case for string encoding, and the change makes the
2-decimal scale that `setScale(2, HALF_UP)` computes with survive all the way to the browser.
The React types (`useGetTaxLiabilityRest.ts`) and the server-side Zod mirror
(`server/api/chat-tools.ts`) moved to `string` in the same change.

**What still holds for numbers.** `test_java_money_crosses_the_wire_without_binary_float_error`
is kept, feeding the model a bare JSON number on purpose: an older cached payload or a replayed
event can still hand one over, and on that path the model must parse it exactly (`0.07` as
`Decimal('0.07')`, never `0.07000000000000001`) rather than through a float. What that path
cannot recover is scale — which is exactly why the wire moved to strings.

### `tenantId` is a contract, asserted on both sides

`Taxpayer.tenant_id` requires a `tenant-` prefix, and so does the Java
`TaxpayerReadModel` constructor (`TENANT_ID_PREFIX`). Neither side assumes it of the other. A
tenant id, a taxpayer id and a bracket id are all opaque strings; in a log line or a
cross-service payload the prefix is the only thing that says which one you are holding. The Java
controller normalises a bare `tenant` JWT claim onto the prefix on the way in, so a token minted
elsewhere cannot write a document this boundary would then reject.

## Secret discipline

`proxy_api_key` is a `SecretStr`, not a `str`. It renders as `**********` in `repr()`, `str()`
and `model_dump()`, so the key survives a naive `LOG.info("settings=%s", settings)` and a crash
traceback that prints locals. `.get_secret_value()` is called in exactly **one** place in the
package — the line in `client.py` that builds the `authorization: Bearer …` header — so there is
one line to audit rather than a search. `tests/test_client.py::test_api_key_never_reaches_a_log_line`
asserts it never reaches a rendered log line.

`.env` is gitignored; only `.env.example` is committed, with placeholder values. `secrets_dir`
points at `/run/secrets`, so a Kubernetes-mounted secret file works with no code change.

## AI authoring discipline

Claude (Opus 5, in Claude Code) scaffolded the first cut of `models.py` and `client.py` in this
repo. `PROMPT_JOURNAL.md` records the decision trail behind both, as the questions the work turned on
and what each resolved to. Concrete deviations between what
the AI-assisted first pass produced and what is committed here:

1. **The API key is `SecretStr`, not `str`.** The reference `settings.py` snippet types
   `proxy_api_key` as a plain `str`, and a plain `str` is one careless format string away from a
   key in a log aggregator: `repr()` prints it, `model_dump()` serialises it, and a traceback
   that dumps locals ships it to whoever reads the crash. `SecretStr` renders as `**********` in
   all three, and `.get_secret_value()` is called in exactly **one** line of this package — the
   `authorization: Bearer …` header in `client.py` — so the audit is one grep, not a review of
   every call site. Two tests hold the line:
   `test_settings.py::test_secret_never_appears_in_repr` and
   `test_client.py::test_api_key_never_reaches_a_log_line`, the latter asserting the key is
   absent from the *rendered* JSON of every emitted log record rather than from the format
   string. See **Secret discipline** above for the deployment half of this.

2. **`List[X]` / `Optional[X]` from `typing` → `list[X]` / `X | None`.** The first cut reached
   for the `typing` aliases out of habit; they have been the deprecated spelling since PEP 585
   and PEP 604 landed, and this package targets 3.12, where the builtins are the only correct
   answer. This is the one deviation that is enforced mechanically rather than by review:
   `select = [..., "UP", ...]` in `[tool.ruff.lint]` turns `List[X]` into a build failure
   (`UP006`/`UP035`/`UP045`), so the habit cannot come back in a later commit and pass CI.
   `grep -RIn 'List\[\|Optional\[\|Union\[' src/` returns 0 matches.

3. **`retry_if_exception_type(httpx.HTTPStatusError)` → a custom `_is_transient` predicate.**
   The reference retry shape retries on *any* `HTTPStatusError`, which means a 400 is retried
   three times. The `if 400 <= status < 500: raise` line inside the `except` block reads like it
   prevents that, but it re-raises the same exception type, which the predicate then matches
   anyway. Committed code retries only on `TimeoutException`, `NetworkError`, and
   `HTTPStatusError` at status >= 500, and two tests pin the attempt counts (exactly 3 on a 503,
   exactly 1 on a 400) so the distinction cannot silently regress.

4. **`dict[str, Any]` → `dict[str, object]`, everywhere.** `Any` is the type that switches type
   checking off for whatever it touches. `disallow_any_explicit = true` in `[tool.mypy]` makes
   that a build failure rather than a habit, and `object` expresses the same "I do not know the
   value type" without the opt-out. `grep -RIn 'Any' src/` returns nothing but a comment.

5. **A hard-coded retry budget → `retry_with(stop=...)` from settings.** The reference
   `@retry(stop=stop_after_attempt(3))` freezes the budget at import time, which makes
   `proxy_max_retries` a setting that exists in `settings.py` and changes nothing. The
   committed client keeps the decorator — it is the readable place to declare a policy, right
   above the function it governs — and copies it per call with `retry_with()`, overriding only
   `stop` from settings. At the default of 3 the copy is identical to the declared policy; the
   knob is live without the policy moving away from the code it protects.

6. **Added: the correlation-id echo check.** The reference client propagates `x-correlation-id`
   outbound and stops there. The Java `CorrelationIdFilter` echoes the header back on every
   response, so the sidecar can *assert* — not assume — that the answer in hand belongs to the
   question it asked. A mismatch now raises instead of being attributed to the wrong taxpayer.

7. **Added: identifiers are never read back out of the model's JSON.** `EstimateCompletion` (the
   object the LLM is asked to produce) deliberately has no `correlationId`, `taxpayerId` or
   `modelId`, and `extra="forbid"` rejects them if the model volunteers them anyway. The client
   composes those from what the process already knows. An LLM is a plausible source of a
   judgement and a terrible source of an identity: a hallucinated id would address the wrong
   taxpayer's record.

8. **Added: every log line carries the ids, including the retry line.** The first cut logged
   retries through a `@staticmethod` that had no access to the `CorrelationContext`, so
   `proxy.call.retry` was the one event in the stream without a `correlation_id` — and a retry
   storm is precisely when a log backend has to be narrowed to a single call. `_log_retry` is
   now a module function that reads the context back off `RetryCallState.kwargs`, which is why
   `_post` takes keyword-only arguments: `state.kwargs` is a stable place to find it, where
   `state.args` positions would shift the moment the signature changed. `httpx`'s own INFO
   line, which carries no ids, is raised to WARNING for the same reason.

9. **The `pydantic.mypy` plugin had to be enabled before `--strict` was meaningful.** Without it
   mypy sees only the synthesised `__init__(**data: Any)` — which `disallow_any_explicit` then
   rejects on every model's class line — and knows nothing about `populate_by_name` aliases.
   This one was found by running the gate, not by reading the code.


## What W7 D2 adds

This sidecar gained the data-and-AI-observability stack today. Five artefacts, one contract:
the embedding dtype, the schema column set, the trace decorator, the eval baseline and the data
validation are a single composite thing. Any one of them missing turns a green build into a
silent retrieval-quality regression or a leaked key, which is why they landed together rather
than as five independently-ticked boxes.

* **`src/taxcalc_ai/corpus.py`** — Pandas corpus loader. De-dups on `(doc_id, chunk_idx)` before
  embedding (not after — that is the key the table's `UNIQUE` constraint and the loader's
  `ON CONFLICT` both resolve on), filters chunks to 1–8000 characters, and emits
  `NDArray[np.float32]` vectors from one batched `encode` call rather than a per-row `df.apply`.
* **`src/taxcalc_ai/pgvector_loader.py`** — psycopg v3 with `register_vector`, `cur.executemany`,
  and `ON CONFLICT (doc_id, chunk_idx, model_version) DO UPDATE` so a retry after a partial
  failure is safe.
* **`src/taxcalc_ai/rag.py`** — `@traceable(run_type="retriever")` on the cosine ANN search,
  streaming to the LangSmith project `taxcalc-ai-dev`.
* **`sql/V001__doc_chunks.sql`** — extended pgvector DDL with the `model_version` column and the
  HNSW `vector_cosine_ops` index.
* **`tests/test_ragas_thresholds.py`** — 50-row golden set with committed
  faithfulness / answer_relevancy / context_precision / context_recall floors.
* **`tests/test_great_expectations_suite.py`** — Testcontainers Postgres + pgvector spin-up and
  the `doc_chunks_v1` checkpoint asserting column non-null, row count, and `chunk_text` length.

### How to run today's additions

```sh
uv sync                                               # picks up the eight new deps
uv run pytest -v tests/test_corpus.py
uv run pytest -v tests/test_pgvector_loader.py        # needs a running Docker daemon
uv run pytest -v tests/test_great_expectations_suite.py
uv run pytest -v -m slow tests/test_ragas_thresholds.py   # needs ANTHROPIC_API_KEY
uv run python -m taxcalc_ai.scripts.assert_langsmith_run_visible  # needs LangSmith creds
```

Four invariants worth stating once:

* Embeddings are `NDArray[np.float32]` end to end — never `float64`. The pgvector wire format
  and the HNSW index both assume the narrower dtype.
* `register_vector(conn)` teaches psycopg the wire format. Without it the insert either fails or
  lands malformed bytes **silently**. Call it before any `vector`-typed insert or select.
* The HNSW op-class (`vector_cosine_ops`) MUST match the operator used at query time (`<=>`). A
  mismatch makes the index unusable and the query falls back to a sequential scan, with no error
  and no warning.
* The CI's LangSmith project is `taxcalc-ai-dev-ci`, not the dev project, so trace uploads from
  noisy gate runs do not pollute the project an engineer reads while debugging.

### `sql/` is deliberately not `src/main/resources/db/migration/`

The DDL is numbered `V001` and lives under `taxcalc-ai/sql/`. It is **not** a Flyway migration
and must not be moved into the Java service's migration directory. Flyway keys applied
migrations by version and validates checksums across the whole history, so a sidecar-owned file
in that path would make this Python project's schema changes able to fail the *Java* service's
context startup. The sidecar applies its own DDL; the `V001` prefix is its own ordering, from
its own beginning.

## AI authoring discipline (W7 D2 additions)

Claude scaffolded the first cut of `corpus.py`, `pgvector_loader.py` and the Great Expectations
suite. Six deviations from that output were corrected before commit. The first two are the ones
the cohort brief predicts; the rest were found by running the gate, not by reading the code.

1. **`np.float64` → `np.float32` at the embedding boundary.** The scaffold typed the embedding
   as a bare `np.ndarray` and never narrowed it, so whatever `encode()` returned went to
   Postgres. `vector(384)` stores 4-byte `real` components: a `float64` array is either rejected
   or silently narrowed on write, and the silent case is the one that hurts — the insert reports
   success, the row exists, and retrieval quality degrades with nothing in the logs to explain
   it. Committed code applies `.astype(np.float32)` once, at the boundary, and `CorpusRow`
   declares `NDArray[np.float32]` so a wider array is a type error rather than a runtime
   surprise. `tests/test_corpus.py` pins it with a stub model that deliberately returns
   `float64`, because the real model already returns `float32` on this backend and therefore
   cannot test the narrowing at all.

2. **`register_vector(conn)` was missing from the loader.** The scaffold opened
   `psycopg.connect(dsn)` and went straight to `executemany`. psycopg does not know what a
   `vector` is — it is an extension type, not a built-in — so the NumPy array goes through
   generic object handling and reaches Postgres as bytes the column either rejects or accepts as
   malformed. The accepting case produces rows that exist, look fine in `SELECT`, and rank
   meaninglessly. It is now the first statement inside every connection in the package.

3. **`langchain-community` had to be pinned `<0.4` before RAGAS would import at all.** Every
   released `ragas` imports `langchain_community.chat_models.vertexai`, which `0.4` removed, so
   the default resolution produces a `ModuleNotFoundError` on `import ragas` — not on use, on
   import. No amount of reading the RAGAS docs surfaces this; it was found by importing it.

4. **The RAGAS evaluator is named explicitly instead of defaulted.** The reference shape,
   `evaluate(dataset, metrics=[...])`, lets RAGAS construct its own LLM and embeddings, and
   those defaults are **OpenAI**. A CI job supplying only `ANTHROPIC_API_KEY` therefore does not
   evaluate against Claude — it fails with an OpenAI authentication error, or, if an
   `OPENAI_API_KEY` happens to be present in the environment, quietly bills a different provider
   and reports scores from a model nobody chose. The committed test builds the evaluator from
   `ChatAnthropic` and passes local MiniLM embeddings, so the only thing crossing the network is
   the judging.

5. **The Great Expectations suite failed on valid data until pgvector's SQLAlchemy type was
   registered.** This one cost the most time and is the most instructive. GX resolves a
   `table.column_types` metric before it can evaluate *any* column-level expectation, and it
   builds that metric by reflecting the table through SQLAlchemy and compiling each column's
   type to a string. SQLAlchemy core has never heard of `vector`, so it reflects `embedding` as
   `NullType()`, and compiling a `NullType` raises `CompileError: Can't generate DDL for
   NullType()`. GX catches that per-expectation and reports `"success": false` with an **empty**
   result dict — so the symptom is four column expectations failing on a perfectly valid corpus,
   with nothing anywhere in the report mentioning a type problem. The row-count expectation
   passes, which makes it look even more like a data issue than a plumbing one. The fix is one
   import (`import pgvector.sqlalchemy`) whose side effect registers `VECTOR` in the Postgres
   dialect's `ischema_names`. Without it the suite is red no matter what the data looks like.

6. **The suite leaked connections, and one leak was fixed rather than silenced.** GX's Postgres
   data source builds a pooled SQLAlchemy engine that nothing in the ephemeral-context lifecycle
   disposes; its connections were then closed by the garbage collector, surfacing as
   `ResourceWarning` from `BaseConnection.__del__` at teardown — which, under this project's
   `filterwarnings = ["error"]`, failed the run long after the assertions had passed. The tests
   now dispose the engine in a `finally`. The one remaining warning exemption is for GX's own
   ephemeral docs-site `TemporaryDirectory`, which GX creates inside `get_context()` and exposes
   no handle to close. A leak this project can reach gets closed; only the one it cannot is
   ignored.

Two further notes on the type gate, which earned its place today more than on any previous day:

* `pandas-stubs` is a new dev dependency. Without it `mypy --strict` refuses the `pandas` import
  outright, so the loader — the single most type-sensitive module added today — was not being
  checked at all.
* `src/taxcalc_ai/py.typed` was missing. The package ships types, but with no PEP 561 marker
  `mypy` on a single test file resolves `taxcalc_ai` through the editable install and treats
  every symbol as `Any`. It surfaced as an `unused-ignore` error, which is a type gate reporting
  that it had nothing to check.

### The uniqueness key is not tenant-scoped, and the loader guards the gap

`UNIQUE (doc_id, chunk_idx, model_version)` — as the brief specifies — does not include
`tenant_id`, so `tenant_id` is not part of the `ON CONFLICT` arbiter either. Left alone, that
means two tenants ingesting the same `doc_id` do not get a row each: the second load's
`DO UPDATE` rewrites `chunk_text` and `embedding` and leaves `tenant_id` untouched, so one
tenant's content ends up stored under another tenant's label — and the tenant-scoped read path
in `rag.py` then serves it to the wrong tenant. An `INSERT` that reports success and leaks data
across a tenant boundary is the worst shape a defect can take here.

Found the hard way: three loader tests failed against a loader behaving exactly as designed,
because the fixtures shared `doc_id`s across tenants.

**The schema is unchanged and the loader closes the hole.** The `ON CONFLICT` clause carries a
guard:

```sql
ON CONFLICT (doc_id, chunk_idx, model_version) DO UPDATE
SET chunk_text = EXCLUDED.chunk_text, embedding = EXCLUDED.embedding
WHERE doc_chunks.tenant_id = EXCLUDED.tenant_id
```

A cross-tenant collision no longer matches, so the update affects zero rows; `load_rows` compares
`cur.rowcount` against the payload size and raises `CrossTenantDocIdError` **before** the commit,
so the batch rolls back whole and the corpus is untouched. The collision became a loud failure at
write time instead of a quiet one at read time, at the cost of no extra round trip.

Widening the key to `(tenant_id, doc_id, chunk_idx, model_version)` is the textbook fix and
remains the right move if the corpus ever becomes genuinely multi-source. It was not taken here
because the narrow key is the schema the deliverable specifies, and the guard removes the hazard
without deviating from it. Three tests pin the behaviour: the raise, the whole-batch rollback,
and — importantly — that a same-tenant reload is still idempotent, because a guard that also
blocked legitimate retries would have quietly removed the property the loader exists to provide.

## What this sidecar does NOT do (yet)

* Production RAG retrieval strategy (re-ranking, hybrid search) — W7 D3. The `doc_chunks` table
  this day built is that lesson's input corpus.
* MCP server publishing — W7 D4, which exposes `retrieve_chunks` as a tool.
* LangGraph orchestration — W7 D5, which reads the `taxcalc-ai-dev` LangSmith project for
  trace-driven debugging and regresses against today's RAGAS baseline.
* Re-embedding the corpus. None of W7 D3–D5 re-embeds; they all assume today's exit criteria.
* An `async` client. `LlmProxyClient` is still synchronous.
