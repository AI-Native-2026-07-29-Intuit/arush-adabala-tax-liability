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
├── src/taxcalc_ai/
│   ├── models.py           # Pydantic v2 boundary models
│   ├── value_types.py      # frozen dataclasses (slots=True) — internal only
│   ├── settings.py         # BaseSettings + SecretStr
│   ├── client.py           # httpx + tenacity + structured JSON logs
│   └── cli.py              # the one place print() is allowed
└── tests/                  # pytest; fixtures/taxpayer_java.json is a captured live response
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
repo. `PROMPT_JOURNAL.md` holds both transcripts verbatim, each verifiable against the commit it first landed in. Concrete deviations between what
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

## What this sidecar does NOT do (yet)

* NumPy + Pandas analytics — W7 D2.
* pgvector retrieval / RAG — W7 D3.
* MCP server publishing — W7 D4.
* LangGraph orchestration — W7 D5.
* An `async` client. `LlmProxyClient` is synchronous today; the `async` variant lands on W7 D2,
  which is why `pytest-asyncio` is already in the dev group with `asyncio_mode = "auto"`.
