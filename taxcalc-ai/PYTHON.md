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
└── tests/                  # pytest; fixtures/taxpayer_java.json is real Jackson output
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

### The one place the two languages do not line up: money on the wire

The round-trip fixture at `tests/fixtures/taxpayer_java.json` is **real Jackson output** — it was
produced by serialising this project's own compiled `TaxpayerReadModel` class with Spring Boot's
date settings, not hand-written to match what Python happens to emit. It shows money as a JSON
**number** with its scale preserved:

```json
{"taxableAmount":120000.00,"liabilityAmount":26400.00}
```

Pydantic reads that into a `Decimal` losslessly *in value*, but re-emits a `Decimal` as a JSON
**string** (`"120000.00"`). Worse for byte-equality: a JSON number's trailing zeros survive
neither parser — `120000.00` parses to `Decimal('120000')`, scale 0.

So a literal byte-for-byte round-trip assertion is not achievable between these two encodings in
either direction, and the only way to make one pass would have been to fake one side of the
fixture. `tests/test_models.py::test_round_trip_against_java_json` asserts the contract that
actually matters instead:

1. every key the Java side emits is consumed (`extra="forbid"` would reject a stray one);
2. every key Pydantic emits is one the Java side emits — the alias map is complete, with no
   snake_case leaking onto the wire;
3. re-validating our own output reproduces an equal model, money included.

**The fix, when it is worth doing:** annotate the Java `BigDecimal` money fields with
`@JsonFormat(shape = STRING)`. The two encodings then become identical and the assertion can be
tightened to raw bytes. It is not done today because it changes the wire format the W4 React
client and the W5 D4 Lambda both already parse, which is a cross-cutting change that deserves its
own PR rather than riding along on a Python deliverable.

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
repo. `PROMPT_JOURNAL.md` holds the unedited session record. Concrete deviations between what
the AI-assisted first pass produced and what is committed here:

1. **`retry_if_exception_type(httpx.HTTPStatusError)` → a custom `_is_transient` predicate.**
   The reference retry shape retries on *any* `HTTPStatusError`, which means a 400 is retried
   three times. The `if 400 <= status < 500: raise` line inside the `except` block reads like it
   prevents that, but it re-raises the same exception type, which the predicate then matches
   anyway. Committed code retries only on `TimeoutException`, `NetworkError`, and
   `HTTPStatusError` at status >= 500, and two tests pin the attempt counts (exactly 3 on a 503,
   exactly 1 on a 400) so the distinction cannot silently regress.

2. **`dict[str, Any]` → `dict[str, object]`, everywhere.** `Any` is the type that switches type
   checking off for whatever it touches. `disallow_any_explicit = true` in `[tool.mypy]` makes
   that a build failure rather than a habit, and `object` expresses the same "I do not know the
   value type" without the opt-out. `grep -RIn 'Any' src/` returns nothing but a comment.

3. **`@retry(stop=stop_after_attempt(3))` decorator → a `Retrying` controller built in
   `__init__`.** A decorator freezes the retry budget at import time, which makes
   `proxy_max_retries` a setting that exists in `settings.py` and changes nothing. Building the
   controller from settings makes it a real knob.

4. **Added: the correlation-id echo check.** The reference client propagates `x-correlation-id`
   outbound and stops there. The Java `CorrelationIdFilter` echoes the header back on every
   response, so the sidecar can *assert* — not assume — that the answer in hand belongs to the
   question it asked. A mismatch now raises instead of being attributed to the wrong taxpayer.

5. **Added: identifiers are never read back out of the model's JSON.** `EstimateCompletion` (the
   object the LLM is asked to produce) deliberately has no `correlationId`, `taxpayerId` or
   `modelId`, and `extra="forbid"` rejects them if the model volunteers them anyway. The client
   composes those from what the process already knows. An LLM is a plausible source of a
   judgement and a terrible source of an identity: a hallucinated id would address the wrong
   taxpayer's record.

6. **The `pydantic.mypy` plugin had to be enabled before `--strict` was meaningful.** Without it
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
