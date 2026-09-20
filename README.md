# arush-adabala-tax-liability
Bootstrap the Tax-Liability Domain

## Tech Stack

- Java 17 (Gradle toolchain)
- Gradle (wrapper included, no local Gradle install required)
- JUnit 5 (Jupiter) for tests

## Package Layout

All classes live under `com.uptimecrew.tax_liability`:

- `model` — domain types: `IncomeEvent`, `IncomeEventDraft`, `IncomeSource`, `Deduction`, `TaxBracket` (Java `record`)
- `service` — behavior over the domain model: `BracketResolver` (interface, returns `Optional<TaxBracket>`), `BracketRegistry` (queryable, immutable store of `TaxBracket` records)
- `exception` — the taxcalc domain exception hierarchy (see Day 4 below)

## Day 3 — Strategy, Factory, DI, Records & Mockito

- `BracketResolver` implementations — three interchangeable strategies, each `final` with `equals`/`hashCode`/`toString`:
  - `FederalBracketResolver` — the federal bracket schedule
  - `FlatStateBracketResolver` — a state that taxes all income at a single flat rate
  - `NoIncomeTaxStateBracketResolver` — a state that levies no income tax
- `BracketResolvers` — static factory (`federal()`, `flatRateState()`, `noIncomeTaxState()`) that returns the `BracketResolver` interface type, not a concrete class
- `TaxLiabilityService` — takes a `BracketResolver` via constructor injection (no `new` inside the service) and exposes `computeLiability(BigDecimal)`, which applies the resolved bracket's rate
- `TaxLiabilityServiceMockitoTest` — proves `TaxLiabilityService` delegates to its injected strategy using a Mockito `@Mock BracketResolver`, without depending on any concrete resolver

## Day 4 — Exceptions, SLF4J Logging & Exception-Path Tests

- `exception` package — a small two-level domain exception hierarchy:
  - `TaxLiabilityException` — abstract, extends `RuntimeException`, exposes `(String)` and `(String, Throwable)` constructors to subclasses
  - `InvalidIncomeException` — `final`; thrown when a caller-supplied taxable amount is invalid (e.g. negative)
  - `BracketResolutionFailedException` — `final`; thrown when an underlying bracket-resolution operation fails, chaining the original cause
- `FederalBracketResolver` and `FlatStateBracketResolver` both throw `InvalidIncomeException` on negative input instead of `IllegalArgumentException`, for consistency across the strategies that raise domain-typed failures; `FlatStateBracketResolver` additionally throws `BracketResolutionFailedException` (wrapping a synthetic `IOException`) when an amount requires its "extended bracket table". `NoIncomeTaxStateBracketResolver` is unchanged from Day 3 and remains on the happy path, still validating with `IllegalArgumentException`.
- `TaxLiabilityService` logs through SLF4J + Logback instead of `java.util.logging`: INFO before delegating and on a successful result, WARN (with the exception passed as the last argument, so the stack trace renders) when its injected strategy throws a `TaxLiabilityException`, then rethrows unchanged. It never catches `RuntimeException`, `Exception`, or `Throwable` — only the domain base.
- `TaxLiabilityServiceExceptionPathTest` — AssertJ + a Logback `ListAppender` attached to the service's logger, proving: the typed exception is thrown, the original cause is preserved, and exactly one WARN log line is emitted containing the exception message
- `TaxLiabilityServiceLoggingTest` — the same `ListAppender` pattern applied to the happy path, proving the service logs exactly one INFO line before delegating and one on a successful result

## Day 5 — TDD, AssertJ, JaCoCo & Test Data Builders

- `ProgressiveStateBracketResolver` — a fourth `BracketResolver` strategy, built test-first, for a state that taxes income progressively across multiple marginal-rate brackets (unlike `FlatStateBracketResolver`'s single flat rate); throws `InvalidIncomeException` on negative input and `BracketResolutionFailedException` for amounts requiring its synthetic "extended bracket table", mirroring the Day 4 exception paths on `FlatStateBracketResolver`
- `ProgressiveStateBracketResolverTest` — AssertJ (`assertThat`/`assertThatThrownBy`) exclusively, every test in explicit Arrange/Act/Assert form with a `methodUnderTest_condition_expectation` name and matching `@DisplayName`
- `TaxBracketTestDataBuilder` (`model` package, production source) — a fluent builder with one `with<Field>` method per `TaxBracket` component, valid defaults, a `build()` method, and a static `aTaxBracket()` factory; several Day 3/Day 4 tests now build their fixtures through it instead of the five-argument constructor
- JaCoCo wired into `build.gradle`: `jacocoTestReport` (HTML + XML) runs after every `test`, and `jacocoTestCoverageVerification` gates `./gradlew check` on a 70% branch-coverage floor

## Week 2 Day 1 — Postgres Schema, Constraints & Transactional Seed

- `db/` — schema-qualified DDL (`V1__schema.sql`), a transactional seed with an intentional-failure test (`V2__seed.sql`), verification SELECTs (`verify.sql`), and an ER diagram with schema decisions and trade-offs (`README.md`); see [`db/README.md`](db/README.md) for details.

## Week 2 Day 2 — Advanced SQL & Testcontainers

- `db/queries/` — four advanced-SQL query files against the Day 1 schema (JOINs, a CTE, window functions, `GROUP BY` + `HAVING`) plus `TaxpayerQueryIT`, a Testcontainers-backed JUnit 5 integration test that proves two of them against a real Postgres 16 container; see [`db/queries/README.md`](db/queries/README.md) for details.

## Week 2 Day 3 — Spring Boot Bootstrap, IoC & @SpringBootTest

- `Application` — the `@SpringBootApplication` entry point at the capstone package root; `build.gradle` gained the Spring Boot + dependency-management plugins and the `web`/`actuator`/`jdbc` starters
- `TaxLiabilityService` is `@Service`; `FederalBracketResolver` is `@Component` + `@Primary` — constructor injection only, no `@Autowired`, no hand-wired `new TaxLiabilityService(...)` in production code
- `config.BracketResolverProperties` / `config.BracketResolverConfig` — the three state-level strategies (`FlatStateBracketResolver`, `NoIncomeTaxStateBracketResolver`, `ProgressiveStateBracketResolver`) are wired as beans through a small `@Configuration` class, with their jurisdiction/rate/bracket values externalized into `application.yml` under `taxcalc.strategies` instead of hard-coded
- `application.yml` — profile-aware (`local` default, `test`), exposes the Actuator `/actuator/health` endpoint
- `ApplicationContextLoadIT` — `@SpringBootTest` proving the context boots and the injected `TaxLiabilityService` bean delegates to the `@Primary` strategy; `BracketResolverConfigIT` proves the three config-bound beans actually resolve to the values declared in `application.yml`, not just that the context doesn't crash; `ActuatorHealthIT` boots the app on a random port and hits `/actuator/health` over real HTTP with `TestRestTemplate`

## Week 2 Day 4 — Spring Data JPA: Entities, Repositories & @DataJpaTest

- `entity` — `Taxpayer`, `Bracket`, `Liability` map the Day 1 `taxcalc.taxpayer`/`bracket`/`liability` tables to `@Entity` classes: schema-qualified `@Table(schema = "taxcalc", ...)`, explicit `@Column` mappings, `BigDecimal` for money and `Instant` for timestamps, a LAZY `@OneToMany`/`@ManyToOne` pair between `Taxpayer` and `Liability`, and `equals`/`hashCode` on the primary key only. `Taxpayer` and `Bracket` each carry a single `TEXT` `id`; `Liability` keeps the composite `(taxpayer_id, tax_year)` primary key from the Day 1 DDL via `LiabilityId` (`@IdClass`) rather than introducing a surrogate id the schema doesn't have — see `Liability`'s Javadoc and `db/README.md`'s "Trade-offs" section for the reasoning.
- `repository` — `TaxpayerRepository`, `BracketRepository`, `LiabilityRepository`, one `JpaRepository` per entity, each with a derived query method and an explicit `@Query` for a lookup the naming convention can't express cleanly.
- `TaxLiabilityService` now takes `TaxpayerRepository` as a second constructor argument; its strategy invocation is `@Transactional`, so resolving a bracket and persisting the resulting `Taxpayer` happen in one transaction.
- `application.yml` gained a `spring.jpa` block: `ddl-auto: validate` (the schema is owned by `db/V1__schema.sql`, never Hibernate) and `open-in-view: false`.
- `TaxpayerRepositoryIT` — `@DataJpaTest` + `@AutoConfigureTestDatabase(replace = NONE)` + a Testcontainers `@ServiceConnection` Postgres container, proving a save → `findById` round-trip and that a derived finder returns only matching rows.

## Week 2 Day 5 — Spring Data MongoDB, Redis Cache & Polyglot Testcontainers

- `readmodel` — `TaxpayerReadModel`, a denormalized `@Document` embedding what the Day 4 JPA side lazily `@OneToMany`-joins, plus `TaxpayerReadModelRepository`, a `MongoRepository` with one derived finder; both the document and its embedded `EmbeddedLiability` implement `Serializable` since the document is also the value type cached behind Redis.
- `TaxLiabilityService` now takes `TaxpayerReadModelRepository` as a third constructor argument: `computeLiability` write-throughs a Mongo projection after the Postgres save, and a new `findById` method reads Redis (via `@Cacheable`) → Mongo → Postgres, in that order. `Application` gained `@EnableCaching`.
- `application.yml` gained `spring.data.mongodb`, `spring.data.redis`, and `spring.cache` (Redis, 60s TTL, no null-value caching) blocks. The `test` profile disables `spring.data.mongodb.auto-index-creation` and the `mongo`/`redis` health indicators, since `@Indexed`-driven auto index creation opens a real Mongo connection while the `MongoTemplate` bean is built (not lazily) — every `@SpringBootTest` that doesn't boot a Mongo/Redis container needed that guard, the same way `management.health.db.enabled: false` already guards the Postgres-only ones.
- **Health endpoint fix**: `management.endpoint.health.show-details` moved from `when-authorized` to `always`. This app has no Spring Security on the classpath, so nothing is ever "authorized" — `when-authorized` was silently hiding every health component, meaning `curl http://localhost:8080/actuator/health` returned only `{"status":"UP"}` with no `mongo`/`redis`/`db` breakdown. `always` makes the component-level detail (e.g. `"mongo":{"status":"UP",...}`) visible on a plain, unauthenticated request, which the Day 5 acceptance checks for Tasks 1 and 3 depend on.
- `TaxpayerPolyglotIT` — one `@SpringBootTest` booting Postgres, Mongo, and Redis Testcontainers in parallel via `@ServiceConnection`, proving the write path lands in both Postgres and Mongo and that a repeated read is served from the Redis cache.
- `TaxpayerReadModelRepositoryIT` — a `@DataMongoTest` against a real Testcontainers Mongo, proving a save → `findById` round-trip and that `findByFilingStatus` returns only taxpayers matching the requested status (not a Mongo transaction, so each test clears the collection in `@AfterEach`).
- `ApplicationContextLoadIT` (W2 D4) gained its own Mongo `@Container`, since its existing `computeLiability` call now exercises the new Mongo write-through path.
- `EmbeddedLiability.equals`/`hashCode` compare every field, not just `(taxYear, bracketId)`: unlike `Taxpayer`/`Bracket`/`Liability`, this is a value object embedded inline rather than an entity with its own id, so two liabilities in the same year and bracket that differ in amount must not compare equal.

## Week 3 Day 1 — Spring Security 7, JWT Resource Server & Rate-Limited LLM API

The W2 D5 read path now sits behind a Spring Security 7 `SecurityFilterChain`: `security.SecurityConfig` validates Bearer JWTs as an OAuth2 Resource Server and maps both the standard `scope` claim and a custom `roles` claim into authorities, `api.TaxpayerController` gates `GET /api/taxpayers/{id}` and the LLM-stub `GET /api/taxpayers/{id}/summary` with `@PreAuthorize`, `security.RateLimitFilter` caps the summary route at 10 requests/minute per JWT subject via Bucket4j (429 + `Retry-After: 60` on exhaustion), and `TaxpayerSecurityIT` proves the full 200/401/403/429 matrix with mocked JWTs against the same Postgres+Mongo+Redis Testcontainers setup as `TaxpayerPolyglotIT`.

## Week 3 Day 2 — REST Maturity, OpenAPI, Feign & Resilience4j

`api.TaxpayerController` moves under `/api/v1/taxpayers` (URI versioning), with `config.OpenApiConfig` publishing a real OpenAPI 3.1 document at `/v3/api-docs` — `@Tag`/`@Operation`/`@ApiResponses` on both routes, a bearer-JWT `SecurityScheme` so Swagger UI (`/swagger-ui.html`) can authorize requests, and both paths added to `SecurityConfig`'s `permitAll()` matcher. The LLM-stub summary route changes from `GET` to `POST /api/v1/taxpayers/{id}/summary` and requires an `Idempotency-Key` header (a UUID); `api.IdempotencyService` makes it POST-once by wrapping the call in a Redis `SETNX` sentinel with a 24h TTL — a cache hit returns the identical stored body, a missing/non-UUID key returns 400, and a key already in flight returns 409 — while the W3 D1 Bucket4j rate limit stays in place underneath it, unrelated concerns. A new `clients` package adds a sibling-service call: `IdentityProfile` (record), `TaxpayerIdentityClient` (`@FeignClient`, declarative HTTP to `identity.base-url`), and `IdentityService`, a `@Service` wrapping the Feign call with `@CircuitBreaker` + a `fallbackProfile` method — the breaker has to live on the wrapper, not the Feign interface itself, since the Feign proxy stack short-circuits before the Resilience4j AOP advisor gets a chance to intercept the call. `contract.IdentityClientCircuitBreakerIT` proves all of it against a WireMock stub on port 8090: the happy path, the breaker tripping OPEN under repeated 5xx responses (and the fallback firing without any further requests reaching WireMock), the idempotent POST resolving the caller's display name through the Feign client, and the OpenAPI document actually exposing the versioned path and security scheme.

## Week 3 Day 3 — Kafka Outbox, Event Consumer & MCP Server

`TaxLiabilityService.computeLiability` now writes a transactional outbox row (`outbox.EventOutboxEntity`, table `taxcalc.event_outbox`) in the SAME `@Transactional` method that persists the `Taxpayer`, so the domain write and the queued event can never diverge; `outbox.OutboxPublisher` is a `@Scheduled` (1s) sweep that publishes unpublished rows to the `taxpayers.events` Kafka topic via `KafkaTemplate`, keyed by aggregate id (`outbox.EventOutboxRepository#findUnpublishedForUpdate` uses `SELECT ... FOR UPDATE SKIP LOCKED` so concurrent publishers split the work instead of double-publishing), and marks a row published only after its send succeeds.

`consumer.TaxpayerUpdatedListener` (`@KafkaListener`, group `taxcalc-read-model-builder`) consumes that same topic and re-projects `TaxpayerReadModel` via a new idempotent `applyEvent` method — applying the same event twice produces the same Mongo document, so at-least-once redelivery is safe. `consumer.KafkaErrorHandlingConfig` pairs the consumer's `ErrorHandlingDeserializer` (wrapping a `JsonDeserializer<TaxpayerUpdatedEvent>`) with a `DefaultErrorHandler` + `DeadLetterPublishingRecoverer`, retrying a bad payload 3 times before routing it to `taxpayers.events.DLT` — that recoverer needs its own `KafkaTemplate<String, byte[]>`, since a deserialization failure republishes the original raw bytes, not a re-serialized object; publishing that byte array through the app's normal String-valued template would `ClassCastException`.

`mcp.TaxpayerMcpServer` exposes one narrow, read-only `@Tool` (`lookupTaxpayer`) over the Model Context Protocol via the Spring AI MCP WebMVC server starter, so an LLM client (e.g. Claude Code, registered via the repo-root `mcp.json`) can query the read model without gaining a write surface; `mcp.McpToolConfig` registers it explicitly via a `ToolCallbackProvider` — the MCP server does not auto-discover `@Tool` methods from component-scanning alone. `SecurityConfig` gained a `permitAll()` for `/sse` and `/mcp/message` (Spring AI's default MCP endpoints), which otherwise fell through to the existing `anyRequest().denyAll()` and returned 403 to any MCP client. `TaxLiabilityService.findById` gained `@Transactional(readOnly = true)`: its Postgres fallback path lazily reads `Taxpayer.liabilities`, which throws outside a Hibernate session once `open-in-view` is off — a pre-existing bug surfaced by driving `lookupTaxpayer` against a real, Mongo-uncached id.

**Schema management also moves to Flyway this day.** Every prior week applied `db/V1__schema.sql`/`V2__seed.sql` by hand-running raw JDBC in each test's `@BeforeAll` (and via `psql` for local runs); that no longer scales once a third migration (the outbox table) needs applying everywhere the first two do. `V1__schema.sql`, `V2__seed.sql` (its "intentional failure" constraint demo moved to `db/README.md`, since a script Flyway runs automatically must succeed cleanly), and the new `V3__event_outbox.sql` now live under `src/main/resources/db/migration/` and are applied by `spring.flyway.*` (on by default once `flyway-core` + `flyway-database-postgresql` are on the classpath — no explicit `application.yml` block needed, since the default `classpath:db/migration` location already matches) every time the app or a `@SpringBootTest`/`@DataJpaTest` context starts, including against the Testcontainers Postgres `@ServiceConnection` substitutes in tests. The `org.flywaydb.flyway` Gradle plugin (pinned to a version compatible with this project's Gradle release, since the Boot-managed flyway-core version references an API Gradle 9 removed) adds `./gradlew flywayInfo`/`flywayMigrate` for inspecting or applying migrations without starting the app. Every manual JDBC schema-application `@BeforeAll` this removed (`AbstractPostgresIT`, `TaxpayerPolyglotIT`, `TaxpayerSecurityIT`, `TaxpayerRepositoryIT`, `IdentityClientCircuitBreakerIT`) — and the now-unused `TestPostgresConnections` retry helper they shared — is gone; `TaxpayerQueryIT` (no Spring context, so no Flyway auto-run) is the one place that still reads the migration files directly via raw JDBC.

`TaxpayerEventFlowIT` proves the whole event chain against four real Testcontainers-managed datastores (Postgres, Mongo, Redis, and a `KafkaContainer`): a domain write reaches Kafka via the outbox (`write_publishes_to_kafka_via_outbox`), a direct Kafka send re-projects the Mongo read model (`consumer_updates_mongo_read_model`), and a malformed payload is routed to the dead-letter topic after retries (`poison_pill_routes_to_dlt_after_retries`).

## Week 3 Day 4 — Spring for GraphQL, DataLoader & Structured Outputs

A new `/graphql` endpoint (Spring for GraphQL, GraphiQL enabled at `/graphiql`) sits alongside the existing REST surface: `src/main/resources/graphql/schema.graphqls` declares `Taxpayer`/`LineItem`/`TaxpayerSummary` types and a `taxpayer(id)` / `latestTaxpayers(limit)` Query plus a `summarizeTaxpayer(id)` Mutation, resolved by `graphql.TaxpayerGraphQlController` (`@QueryMapping` × 2, `@MutationMapping` × 1). `TaxLiabilityService` gained `findLatest(int)` (paged, newest-`createdAt`-first, Mongo only — no Postgres fallback, unlike `findById`) to back the new query.

**The N+1 fix (`@BatchMapping`) turned out to need no query at all.** `TaxLiabilityService.loadLineItemsByParent` groups each batch of taxpayers' line items in one pass, wired to `Taxpayer.lines` via `@BatchMapping(typeName = "Taxpayer", field = "lines")` on the controller — but because `TaxpayerReadModel` (W2 D5) already embeds its liabilities inline, resolving `lines` costs zero additional Mongo or Postgres round-trips, not the "one batched query" the pattern usually eliminates the N+1 down to. Verified live: seeded 6 taxpayers, ran `{ latestTaxpayers(limit: 50) { id lines { id } } }` with Hibernate SQL logging on, and confirmed zero `org.hibernate.SQL` lines on the request thread.

`llm.LlmSummaryService` backs the `summarizeTaxpayer` mutation with a real Spring AI structured-output call: builds a prompt from the `TaxpayerReadModel` document, calls `chatClient.prompt().user(prompt).call().entity(TaxpayerSummary.class)`, then re-validates the result against a hand-written JSON Schema (`resources/schemas/TaxpayerSummary.schema.json`, `com.networknt:json-schema-validator`) so a model response that parses but violates a value constraint (an out-of-enum `riskBand`, a negative `totalLiability`) still fails loudly. `build.gradle` gained `spring-ai-starter-model-anthropic` (pinned to the same 1.0.x GA line as the existing MCP starter) and `application.yml` a `spring.ai.anthropic` block (`ANTHROPIC_API_KEY`, defaulting to `dummy` for local smoke-testing — verified live that the dummy key produces a genuine Anthropic authentication failure, not a silent no-op, so a real key is required to exercise the mutation outside of tests).

`TaxpayerGraphQlIT` proves all three legs against a real Spring for GraphQL server (`@AutoConfigureGraphQlTester`, Postgres + Mongo + Redis Testcontainers): a plain query, the batch-mapped `lines` field, and the structured-output mutation re-validated against the JSON Schema. Its `StubChatModelConfig` swaps in a mocked `ChatModel` (not a mocked `ChatClient`) behind a real `ChatClient.Builder`, so the test still exercises Spring AI's actual structured-output parsing instead of just returning a canned object, without ever calling Anthropic.

**Also this day:** `mcp.McpToolConfig` (W3 D3) gained an `ApplicationReadyEvent` listener logging `"MCP server started, tools registered: {n}"` — Spring AI's MCP auto-configuration never logged that literal phrase itself (it logs `Registered tools: N` instead), so smoke-tests grepping for it had nothing to match; the app now emits its own stable, version-independent confirmation line.

## Week 3 Day 5 — OpenTelemetry, Multi-Agent TDD & Trace Continuity

OpenTelemetry auto-instrumentation (`io.opentelemetry.instrumentation:opentelemetry-spring-boot-starter`) now wires HTTP, JDBC, and (via the separate `opentelemetry-spring-kafka-2.7` module) Kafka producer/consumer spans, exported OTLP/HTTP to a local Jaeger. `kafka.TraceparentLoggingProducerListener` logs the `traceparent` header the instrumentation injects into every outbox-published record, so propagation is visible in the `bootRun` log without opening Jaeger. `llm.LlmSummaryService`'s Anthropic call is wrapped in a manual `llm.summarize` span (Spring AI's client isn't auto-instrumented), carrying `llm.model` / `llm.tokens.in` / `llm.tokens.out` attributes for cost attribution.

**Trace continuity through the outbox's async boundary is a real architectural fix, not just wiring.** `outbox.OutboxPublisher`'s `@Scheduled` sweep runs on its own background thread with no span inherited from whatever request wrote the outbox row - left alone, every Kafka send it makes starts a fresh, disconnected trace regardless of what triggered the write. `outbox.EventOutboxEntity` now carries a nullable `trace_parent` column (`V4__event_outbox_trace_context.sql`); `TaxLiabilityService#captureTraceParent` injects the writing request's current OTel context into it via the configured `TextMapPropagator`, and `OutboxPublisher` extracts and restores that context as the parent around each row's Kafka send - so the auto-instrumented producer span, and the consumer span downstream of it, land in the SAME trace as the original request. A new `POST /api/v1/taxpayers` (`api.CreateTaxpayerRequest`, secured with a narrower `taxpayers.write` scope / `TAXPAYER_WRITER` role rather than reusing read access) is what actually exercises this path from the outside.

`TaxpayerObservabilityIT` proves all of it programmatically against an `InMemorySpanExporter`-backed SDK instead of eyeballing Jaeger, across five real Testcontainers (Postgres, Mongo, Redis, Kafka, and a Jaeger all-in-one kept for parity even though nothing reads from it - the in-memory exporter is the actual source of truth): an HTTP `GET`'s JDBC-fallback child span shares its server span's trace id; the new `POST` walks outbox → Kafka → consumer → Mongo with **every** span - five or more of them, including both a producer and a consumer span - sharing the ONE trace id the request started; and the `llm.summarize` span carries non-null token attributes. The `test` profile disables the OTel SDK by default (`otel.sdk.disabled: true`) so every other IT stays quiet, with this one class (`webEnvironment = RANDOM_PORT`, `@AutoConfigureMockMvc` still available for the secured `GET`) overriding it back on. Its LLM stub and `TaxpayerGraphQlIT`'s (W3 D4) now share one extracted `llm.StubChatClientFactory` instead of each hand-rolling the same `ChatModel` mock.

Three real bugs surfaced while wiring this up, none of them in the code this deliverable was actually about: `spring-cloud-dependencies`' BOM silently pins `io.opentelemetry:*` to a version missing a class the instrumentation modules need at JDBC-wrap time (fixed by re-importing `io.opentelemetry:opentelemetry-bom:1.44.1` directly in this project's own `dependencyManagement` block, which overrides the transitive pin); `KafkaErrorHandlingConfig`'s hand-built `DefaultKafkaProducerFactory` bypassed the `DefaultKafkaProducerFactoryCustomizer` callback the OTel Kafka instrumentation relies on to inject `traceparent` headers, silently producing untraceable Kafka sends until customizers were applied manually; and a manually-constructed `OpenTelemetrySdk` in the test config defaulted to a no-op `ContextPropagators`, silently breaking header injection/extraction on both ends of the Kafka leg until `setPropagators(...)` was added explicitly.

Also ships one small feature — a `tags: [String!]!` field on `Taxpayer` plus a `taxpayersByTag` query — through a 3-agent workflow (generator → tester → reviewer), each agent handed only the previous agent's output. The reviewer caught a real bug the generator and tester both missed: `TaxpayerReadModel`'s no-arg constructor (required by Spring Data Mongo, which populates fields via reflection off it rather than the parameterized constructor) left `tags` as `null` for any Mongo document written before this field existed, which the non-null GraphQL field would then reject — fixed with a `= List.of()` field initializer.

## Week 4 Day 1 — Modern React with Vite & Strict TypeScript

`taxcalc-web/` is a new, separately-managed pnpm project peer to the Spring Boot capstone — nothing under `src/` changed this day. Scaffolded with Vite 6 + React 19 + TypeScript, `.nvmrc` pins Node 20, and `tsconfig.json` turns on `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `verbatimModuleSyntax`, and `noImplicitOverride` from the start rather than backfilling them later. `eslint.config.js` (ESLint 9 flat config) enforces `react-hooks/rules-of-hooks` and `@typescript-eslint/no-explicit-any` as errors, `react-hooks/exhaustive-deps` as a warning.

`pages.TaxpayerDetailPage` reads a stubbed taxpayer read-model (`public/mocks/taxpayer.json`, shaped after the W2 D5 Mongo document: `filingStatus`/`jurisdictionCount`/`totalLiability`/`lines`, with `totalLiability` and each line's `amount` kept as strings — mirroring this project's own BigDecimal-for-money convention, since JS `number` is IEEE-754 binary64 and loses cents at scale) through `hooks.useTaxpayer`, a custom hook that threads a discriminated-union `State` (`loading` | `ok` | `error`) through `useState` and fetches inside `useEffect`. The page owns a `threshold` `useState<number>(50)` and passes it down to two siblings — a controlled `ThresholdSlider` (`value`/`onChange`) and a read-only `ThresholdReadout` (`value`) — the canonical lifted-state demonstration: two children, one source of truth in the parent. `App.tsx` hash-routes `#/taxpayers/stub-id-1` to `TaxpayerDetailPage` and renders a placeholder otherwise; this is a deliberate stand-in ahead of TanStack Router, which replaces it on W4 D3 along with the mock JSON read swapped for a real Apollo Client query against the W3 D4 `/graphql` endpoint.

`vitest.config.ts` (jsdom environment, `src/test/setup.ts` importing `@testing-library/jest-dom`) backs two smoke tests in `src/test/TaxpayerDetailPage.test.tsx`: a stubbed-`fetch` render assertion, and a slider-drives-readout assertion using `fireEvent.change` rather than the more natural `userEvent.keyboard('{ArrowRight}')` — verified directly that jsdom doesn't implement native arrow-key stepping for `<input type="range">` (a layout-engine "default action" jsdom doesn't emulate), so the keyboard-driven version would silently exercise nothing. `.github/workflows/web-ci.yml` (repo root, not under `taxcalc-web/`, since GitHub only discovers workflows there) runs `pnpm install --frozen-lockfile` → `lint` → `typecheck` → `test` → `build` on every PR touching `taxcalc-web/**`, ordered so a lint/typecheck/test failure short-circuits the bundle step.

**Known, deliberate gap:** `taxcalc-web/`'s files were hand-authored to the required shape rather than left as raw `pnpm create vite@latest` output — checked `create-vite` versions 4.0 through 9.x and no version ever ships both a single `tsconfig.json` and an ESLint 9 flat config together (the tsconfig split at 5.3 predates flat config's introduction at 5.5), so the two Task 1/Task 2 instructions can only both be satisfied by editing past the scaffold, not by leaving it untouched.

## Week 4 Day 2 — React Hooks, Zustand & Error Boundaries

Cross-cutting state moves out of `TaxpayerDetailPage`'s local `useState`
into `stores.useTaxpayerFilterStore` (`taxcalc-web/src/stores/`): a typed
Zustand store holding `filingStatusFilter`, `dateRange`, `searchText`,
`includeArchived`, and the W4 D1 `threshold` field, plus four setters and a
`reset` action. Every `set()` call carries a named action string
(`'filters/setSearchText'`, etc.) via `devtools`, so the Redux DevTools
timeline reads like a log instead of an anonymous diff; `persist`'s
`partialize` keeps only `threshold` across reloads — persisting `searchText`
would silently re-filter results the next time the page loads for an
unrelated reason. The store binds to a small `safeLocalStorage` wrapper
(try/catch around `window.localStorage`, falling back to an in-memory
`Map`) rather than the bare `localStorage` global directly: Safari private
browsing throws `SecurityError` on `setItem`, and — hit live while building
this — Node 20+'s own experimental `localStorage` global can shadow jsdom's
working implementation under Vitest, leaving `window.localStorage`
`undefined`. `components.ThresholdSlider` and `components.ThresholdReadout`
now read/write the `threshold` slice directly instead of taking
`value`/`onChange` props, and a new `components.FilterStrip` renders one
control per filter field above the detail card, each subscribing to its own
slice so an edit to one field doesn't re-render the others.

`pages.TaxpayerDetailPage`'s W4 D1 `useTaxpayer`-derived `data`/`loading`/
`error` shape is replaced by `useReducer` over a pure, separately-testable
reducer in `pages.TaxpayerDetailPage.reducer`: a five-state discriminated
union (`idle | loading | success | error | empty`) with a
`const _exhaustive: never = action` guard on the reducer's default branch,
so a future action variant added without a matching `case` fails to
compile rather than silently falling through. The page's own effect drives
it — dispatching `fetch/start` up front, then `fetch/success` or
`fetch/error` on resolution — deliberately shaped to match 1:1 with the
`data`/`error` result Apollo Client's query hook returns once it replaces
this stub fetch on W4 D3. (`hooks.useTaxpayer` from W4 D1 is left unchanged
and is no longer imported by the page — dead code today, removed rather
than resurrected once Apollo lands.) A new `hooks.useDebouncedSearch` reads
the store's `searchText` slice, lags it by a configurable `delayMs` behind
a `useEffect`-owned `setTimeout`, and returns a cleanup that clears the
pending timer — without that cleanup, a stale timer from a superseded
keystroke would still fire after the component (or the next keystroke)
moved on. The page wires it into a "filtering for: '...'" readout.

`components.ErrorBoundary` is a class component (React 19 still has no
hook-based equivalent) implementing `static getDerivedStateFromError` +
`componentDidCatch`, taking a `(error, reset) => ReactNode` fallback render
prop rather than fixed markup. `App.tsx` wraps `TaxpayerDetailPage` in it;
the fallback renders a `role="alert"` error card with the message in a
`<pre>` and a retry button calling `reset()`, which re-mounts the
descendants. The page also gets a dev-only "Trigger error" button, gated on
`import.meta.env.DEV` (backed by a new `src/vite-env.d.ts` for the
`ImportMeta` typing) — it sets state and lets the following render do the
throwing, since error boundaries only catch errors thrown during rendering,
not ones thrown from inside an event handler.

Thirteen Vitest tests now pass (the two W4 D1 smoke tests, unchanged, plus
eleven new): `TaxpayerDetailPage.reducer.test.ts` drives the reducer as a
pure function through all five states and `reset`; `useTaxpayerFilterStore.test.ts`
resets the store via `setState(getInitialState(), true)` in `beforeEach` so
tests don't bleed into each other, covering each setter (including
last-write-wins on `setFilingStatusFilter`) and `reset()`;
`useDebouncedSearch.test.tsx` uses `vi.useFakeTimers()` to prove the lag and
that a mid-stream `searchText` change cancels the prior pending timer
instead of racing it. `pnpm typecheck && pnpm lint && pnpm test && pnpm build`
all pass locally, and the W4 D1 GitHub Action re-runs unmodified.

## Week 4 Day 3 — Apollo Client, TanStack Query, React Router v7 & MSW

`taxcalc-web/` cuts over from the W4 D1/D2 mock-JSON stub to the two live
backends. `apollo/client.ts` builds an `ApolloClient` (pinned to `^3.11`,
not the newly-released v4, to stay on the APIs this deliverable's spec
assumes) with a typed `InMemoryCache` (`Taxpayer: { keyFields: ['id'] }`)
and a `setContext` auth link that attaches `Authorization: Bearer <jwt>`
from `localStorage`. `codegen.ts` (`config: { useTypeImports: true }` on
both outputs below, so generated code satisfies `verbatimModuleSyntax`)
points its `schema` at the backend's own checked-in
`src/main/resources/graphql/schema.graphqls` rather than introspecting a
running `/graphql` server — Docker wasn't available while building this.
It writes two outputs from the same two documents
(`queries/LatestTaxpayers.graphql`, `queries/SummarizeTaxpayer.graphql`):
`src/gql/generated/` via `@graphql-codegen/client-preset` (typed
`TypedDocumentNode`s + fragment masking), and `src/gql/generated/hooks.ts`
via the classic `typescript`/`typescript-operations`/
`typescript-react-apollo` plugin trio, which generates the named
`useLatestTaxpayersQuery`/`useSummarizeTaxpayerMutation` hooks the pages
below actually call — the client preset alone doesn't generate hooks by
design (Apollo's own `useQuery`/`useMutation` are meant to infer
everything from a `TypedDocumentNode` directly), so the second output
exists specifically to get named-hook call sites instead of that pattern.

`pages.TaxpayerListPage` renders the `latestTaxpayers` query's
loading/error/data branches (the schema's `Taxpayer` type only has
`id`/`tags`/`lines` — no `name` or `updatedAt`, unlike the generic
deliverable spec's reference shape) as `<a href="/taxpayers/{id}">`
anchors, matching the deliverable's literal markup rather than a
React-Router `<Link>` (a full page reload on click, traded for hitting
the spec exactly). `pages.TaxpayerSummaryPage` calls the
`summarizeTaxpayer` mutation with an `optimisticResponse` tagged
`__typename: 'TaxpayerSummary'` so Apollo's cache can normalize the
eventual server write — but empirically, the mutation hook's own `data`
never reflects that optimistic value; `optimisticResponse` only updates
cache entries a `useQuery` elsewhere is watching, and `TaxpayerSummary`
has no such query (it's reachable only via this mutation). The page's
"instant placeholder" is keyed off `loading` (which *does* flip
synchronously) instead, with `optimisticResponse` left in place for
whichever future consumer actually queries this data.

`hooks.useGetTaxLiabilityRest` is a TanStack Query hook (`queryKey:
['taxcalc', id]`, `enabled: Boolean(id)`, one-minute `staleTime` matching
the backend's Redis cache TTL) against the real `GET
/api/v1/taxpayers/{id}`. Its `TaxpayerRest` type mirrors
`TaxpayerReadModel`'s actual JSON shape (`id`/`displayName`/`filingStatus`/
`homeJurisdiction`/`createdAt`/`liabilities`/`tags`), not the deliverable
spec's generic placeholder fields — and its `taxableAmount`/
`liabilityAmount` are typed `string`, the BigDecimal-as-string convention
the rest of this codebase uses. (They were `number` until W7 D1, when
`TaxpayerReadModel` gained `@JsonFormat(shape = STRING)` on both money
fields: JavaScript has one numeric type, IEEE-754 double, so `JSON.parse`
turned `120000.00` into a float before any code here saw it. Format these
for display; do not pass them through `Number()`.) A 404 resolves to `null` data instead of throwing, so
`pages.TaxpayerDetailPage`'s W4 D2 `useReducer` state machine keeps
treating "not found" as its own `empty` state rather than folding it into
`error` — the page now reads `:id` via `useParams` and drives that same
reducer off the query's `data`/`isLoading`/`isError` instead of its own
fetch effect. `router.tsx` (`createBrowserRouter`) replaces the W4 D1/D2
hash-routing placeholder: a `ProtectedLayout` redirects to `/login` when
`uc:jwt` is absent from `localStorage`, otherwise renders its children via
`Outlet`; `pages.LoginPage` is a stub that writes a fake token and
navigates to `/taxpayers` — real validation happens at the backend's own
OAuth2 resource server, not this client-side presence check.

Reading/writing `uc:jwt` moved into a shared `lib/jwtStorage.ts`
(`getStoredJwt`/`setStoredJwt`) used by the Apollo auth link, the REST
hook, `ProtectedLayout`, and `LoginPage` — extracted after discovering
`window.localStorage` is genuinely `undefined` under this Node 20+/jsdom/
Vitest combination (the same issue `useTaxpayerFilterStore.ts`'s
`safeLocalStorage` already worked around), so every read/write goes
through one try/catch instead of four ad-hoc ones. `hooks/useTaxpayer.ts`,
`types/taxpayer.ts`, and `public/mocks/taxpayer.json` are deleted: dead
code once `TaxpayerDetailPage` fetches live data, exactly as the W4 D2
notes above flagged they would be.

`test/handlers.ts` + `test/server.ts` add MSW as the network seam for
Vitest: `graphql.query`/`graphql.mutation` handlers back the two Apollo
operations (matched by operation name, independent of endpoint URL) and
an `http.get` handler backs the REST endpoint, all installed via
`setupServer(...).listen({ onUnhandledRequest: 'error' })` so an
un-mocked call fails the test instead of hanging. Getting this working
under jsdom took one real fix: jsdom ships its own `AbortController`/
`AbortSignal` (the DOM spec requires them), distinct from the class
Node's native `fetch` — which MSW's node interceptor patches — validates
a `signal` against internally; Apollo's `HttpLink` builds an
`AbortController` per request for cancellation, and passing its `.signal`
through tripped "Expected signal to be an instance of AbortSignal" on
every Apollo-backed test. `server.ts`'s `beforeAll` wraps the
interceptor's already-patched `fetch` to strip an incompatible `signal`
before it reaches the real request — no test here exercises cancellation,
so this is simpler than faking it across two `AbortSignal` realms. (W4 D4
later replaces the strip-and-drop with a real fix once a test needs
genuine cancellation - see that section.) New
specs cover `TaxpayerListPage`, `TaxpayerSummaryPage` (including the
loading-placeholder timing, using a deliberate MSW `delay(200)` so the
test has a real window to observe it before the mocked response resolves),
`ProtectedLayout` (redirect vs. pass-through, using a local
`vi.stubGlobal('localStorage', ...)` polyfill for the same jsdom-undefined
reason), and `useGetTaxLiabilityRest` (success, `enabled: Boolean(id)`,
and the 404→`null` case) — plus a matching 404 case in
`TaxpayerDetailPage.test.tsx`. 21 Vitest tests now pass, up from 13.
`pnpm typecheck && pnpm lint && pnpm test && pnpm build` all pass locally.

## Week 4 Day 4 — Vercel AI SDK, Streaming Responses, Streamed Tool Calls & MSW SSE Tests

`taxcalc-web` replaces W4 D3's blocking `summarizeTaxpayer` mutation with a
streaming chat assistant. `server/` is new: a thin Hono app (`pnpm server`,
`:3001`) whose one route, `server/api/chat.ts`, holds the only code in this
app that talks to an LLM. It calls `streamText` against
`createOpenAICompatible({ baseURL: 'http://localhost:8080/ai' })` — the W3
D4 Spring AI backend's OpenAI-compatible endpoint, never a real provider —
and returns `result.toDataStreamResponse()` with explicit
`text/event-stream` / `no-cache, no-transform` / `X-Accel-Buffering: no`
headers, forwarding the incoming request's `AbortSignal` so a client
disconnect cancels the upstream call too. `vite.config.ts`'s `server.proxy`
forwards the browser's `/api/chat` to that Hono port, so `TaxpayerChatPanel`
(mounted at `/taxpayers/:id/chat`) never needs its own base URL.

`ai`/`@ai-sdk/react`/`@ai-sdk/openai-compatible` are pinned to the `4.x`/
`1.x`/`0.x` line respectively — `pnpm add` without a version resolved `ai`
7.0.79, whose `useChat` is a rewritten `Chat`-class API with no
`input`/`handleSubmit`/`isLoading`/`toolInvocations`, none of which match
this deliverable's spec; same pinning rationale W4 D3's README section gave
for Apollo Client. `@hono/node-server` (needed for `serve()` to actually
listen under Node — Hono itself is runtime-agnostic and the lesson's
package list omitted it) and `tsx` (`pnpm server` runs `tsx watch
server/index.ts`) round out the new dependencies.

Task 2 wires `TaxpayerChatPanel`'s Stop (`stop()`, disabled unless
`isLoading`), Regenerate (`reload()`), a `role="status"` spinner, a
`role="alert"` error pane, and scroll-to-bottom on every `messages` change.
`chat.ts` pairs this with two layers of error handling. The 5xx-mapping
piece is `mapUpstreamErrors`, a custom `fetch` passed to
`createOpenAICompatible({ fetch })` — the AI SDK's own doc comment on that
option calls it out as exactly this: "a custom fetch implementation you can
use as a middleware to intercept requests." It inspects every response from
the Spring AI backend before the SDK's stream decoder ever sees it; a
4xx/5xx becomes one well-typed `UpstreamStatusError` instead of an opaque
parse failure, logged server-side with the real status/body and re-thrown
with an already-client-safe message. `toClientErrorMessage` (passed as
`toDataStreamResponse`'s `getErrorMessage`) is the layer beneath that: it
uses `UpstreamStatusError`'s message verbatim when present, and falls back
to the same generic message for anything the fetch middleware never saw at
all — a connection refused because no Spring AI container is running or
checked into this repo, DNS failure, timeout — cases where `fetch()` itself
rejects before there's a `Response` to inspect, so they fall through to
`streamText`'s own retry/error handling instead. Both paths were verified
against a hand-rolled Node `http` stub standing in for the backend: a
genuine `500` with a JSON error body reaches `mapUpstreamErrors` in exactly
one request (no retries, since a thrown `UpstreamStatusError` isn't the
`APICallError` shape the SDK's retry logic re-attempts), while killing the
stub entirely reproduces the original `ECONNREFUSED`-after-three-attempts
path unchanged.

Task 3 adds `server/api/chat-tools.ts`: `lookupTaxpayer`/`estimateLiability`,
zod-typed `ai` tools executed server-side against the W3 D2 REST backend
(the browser never calls that backend through this path), wired into
`streamText` via `tools`/`maxSteps: 3`, with the system prompt taught when
to call each rather than let the model fabricate taxpayer data.
`estimateLiability`'s `GET /api/v1/taxpayers?year=` target doesn't exist on
the current `TaxpayerController` (only `GET /{id}` does) — the same
"prerequisite piece isn't actually built yet" situation the W3 D4 Spring AI
`/ai/chat` endpoint and a docker-compose for it are in; neither exists
anywhere in this repo's history, so today's work targets them as documented
contracts rather than a live integration. `ToolCallCard` renders one
`ToolInvocation`'s name/args/result inline, mapped from each message's
`toolInvocations`. `useTaxpayerChatStore` (Zustand + `persist`, key
`uc:taxpayer-chat`) seeds `useChat`'s `initialMessages` on mount and is
written to only from `onFinish` — never from a per-token callback, which
would both tank streaming FPS and let a reload mid-stream rehydrate a
message that never finished; confirmed against the `@ai-sdk/ui-utils`
source that an aborted request never reaches `onFinish` at all, so Stop
can't leak a partial message into storage by construction. Wiring this up
surfaced a real bug: `useTaxpayerChatStore`'s `persist` initially wrote
nowhere, because `window.localStorage` is genuinely `undefined` under this
Node/jsdom/Vitest combination (confirmed by direct probe) — the exact
failure `useTaxpayerFilterStore`'s local `safeLocalStorage` fallback
already worked around. Extracted that fallback into `src/lib/
safeLocalStorage.ts` and pointed both stores at it, rather than leaving the
new one silently broken.

Task 4's `src/test/sse-handlers.ts` hand-encodes the Vercel AI SDK's
data-stream protocol (`0` text delta, `9`/`a` tool call/result, `d` finish
message — read directly from `@ai-sdk/ui-utils`'s own parser rather than
guessed, since a wrong prefix fails silently client-side instead of raising
a test error, and encoded with one shared `TextEncoder` reused per frame)
so the whole chat UI is testable with no Hono process and no Spring AI
backend running; spread into `test/handlers.ts` alongside the existing
REST/GraphQL handlers. Four spec files cover: `TaxpayerChatPanel.test.tsx`
(streamed-token rendering with an explicit `data-role="assistant"` check,
Stop mid-stream, Regenerate firing a second POST, a tool-call turn
rendering a `ToolCallCard` through `partial-call → call → result`, reload
rehydration, Send/Regenerate disabled-state wiring, and the
`scrollIntoView` effect), `TaxpayerChatPanel.error.test.tsx` (both a `5xx`
`server.use` override and a network-level `HttpResponse.error()` override,
each rendering the `role="alert"` pane), `ToolCallCard.test.tsx` (all three
`ToolInvocation` states), and `useTaxpayerChatStore.test.ts` (insertion
order across multiple appends, plus a real persist round-trip: append a
message, build a second store against the same storage, assert it
rehydrates). `Element.scrollIntoView` needed a one-line stub in
`test/setup.ts` — jsdom does no layout, so it's simply unimplemented.

Genuinely verifying that Stop interrupts an in-flight stream looked
impossible at first: it hits the identical jsdom `AbortController`/
`AbortSignal` cross-realm gap the W4 D3 section above documents for
Apollo's `HttpLink`, and `test/server.ts`'s existing fetch wrapper —
needed so MSW's interceptor doesn't reject the incompatible signal
outright — stripped `init.signal` from every request before it reached the
network, disabling cancellation entirely rather than just working around
the crash. Tracing the actual error (undici's webidl `AbortSignal`
converter, `MakeTypeAssertion`, doing a strict `instanceof` check against
its own module-scoped reference — read from `undici/lib/web/webidl/
index.js`, not assumed) confirmed the two classes can never be unified
from test code: vitest's jsdom environment setup hardcodes
`AbortController`/`AbortSignal` into the fixed list of globals it copies
from `window`, unconditionally overwriting Node's native ones for every
test file, with no supported opt-out. So `server.ts`'s wrapper now does
something different: strip the incompatible signal before the real fetch
call as before, but reimplement cancellation itself at the response
body-stream level — once the caller's real signal fires, the wrapped
stream errors with a plain `Error` named `'AbortError'`, the only thing
`@ai-sdk/provider-utils`'s `isAbortError` actually checks
(`error instanceof Error && error.name === 'AbortError'`, no class-identity
check at all). That's enough for `useChat`'s `stop()` — and Apollo's own
cancellation, retroactively — to genuinely interrupt an in-flight request
under test, not just document that it can't be verified. Confirmed
deterministic across repeated runs and, since the fix touches shared test
infrastructure rather than anything Node-version-specific, re-verified
under Node 20.20.2 (installed locally via `brew install node@20`,
keg-only) to match `.github/workflows/web-ci.yml`'s pinned version exactly
rather than only the newer Node this was developed against. 40 Vitest
tests now pass, up from 22, hitting the deliverable's "≥ 40" target.
`pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm
test && pnpm build` — the exact sequence `.github/workflows/web-ci.yml`
runs — all pass locally, under both Node versions.

**Follow-up: `dev/stub-spring-ai.ts`.** Everything above verifies the chat
proxy's plumbing, but none of it demonstrates the actual happy path in a
browser, since no Spring AI backend or docker-compose for it exists
anywhere in this repo. Added a dev-only, hand-rolled stand-in (`pnpm
stub-backend`, `:8080`, not committed as any kind of real backend
implementation) that speaks the genuine OpenAI-compatible chat-completions
wire format `@ai-sdk/openai-compatible` expects — not the Vercel
data-stream protocol the browser sees, one level further upstream, so
`streamText`'s real parsing path runs end-to-end rather than being
bypassed by a mock. It detects a `role: "tool"` message in the incoming
request (step two of a tool-calling exchange) versus a fresh user message
mentioning "lookup" (triggering a canned `lookupTaxpayer` tool call) and
streams a plain-text reply either way; `GET /api/v1/taxpayers/:id` and
`GET /api/v1/taxpayers?year=` return canned JSON for the two tools'
`execute()` calls. Driven through a real Chromium session (`pnpm dev` +
`pnpm server` + `pnpm stub-backend`): typing a plain message renders real
streamed tokens ("Hello from the stub tax assistant.") word by word, and
a message containing "lookup" renders a genuine two-step exchange -
`ToolCallCard` shows `lookupTaxpayer` with its args, then the REST result,
then a real follow-up reply ("Found stub taxpayer stub-1.") - all through
the actual production code path, not MSW. (One side effect worth noting:
`useTaxpayerChatStore`'s single flat, non-taxpayer-scoped `messages` array
means a second taxpayer's chat panel shows the first taxpayer's completed
turns too on first mount, in the same browser session — the exact design
question raised separately about whether that store should be keyed by
taxpayer id.)

**Follow-up: request/response validation.** Neither `chat.ts` nor
`chat-tools.ts` validated its input before this - the former took `{
messages }` straight off the wire (and `:3001` has no auth or origin
restriction, so anything that can reach it directly could POST arbitrary
JSON), the latter returned a REST response's body untouched regardless of
shape. Fixed with the same two-layer, log-the-real-cause pattern the
5xx-mapping middleware already established, but split into two genuinely
different failure classes rather than one shared path: `chat.ts`'s new
`chatRequestBodySchema` (zod, `.passthrough()` so `id`/`toolInvocations`/
etc. survive untouched) rejects a malformed request with a plain `400`
before any stream opens - there's nothing to layer an SSE sentinel frame
onto yet, so reusing that machinery here would have been the wrong shape
for the failure. `chat-tools.ts`'s two new schemas (`taxpayerRestSchema`,
mirroring `useGetTaxLiabilityRest.ts`'s already-established `TaxpayerRest`
type field-for-field; `liabilityEstimateListSchema`, formalizing
`dev/stub-spring-ai.ts`'s own shape since no real backend exists to check
it against) validate each tool's REST response before returning it as the
tool's result. A failure there throws `ToolResponseValidationError`,
which the AI SDK wraps in `ToolExecutionError` and re-throws (confirmed by
reading `ai`'s `executeTools`: there's no separate "let the model see a
tool failure and react" path in this SDK version), so it still reaches
`toClientErrorMessage` the same way an upstream connectivity failure
does - unwrapped there via `.cause` so the log stays specific even though
both end up behind the same generic client-facing message. 11 new tests
(`chat.test.ts`, exercising the Hono route directly via its own
`.request()` helper; `chat-tools.test.ts`, covering both tools' happy and
malformed-response paths via MSW) bring the project to 51 Vitest tests;
verified live end-to-end too, including that a well-formed body still
reaches a real tool call through the running proxy + stub backend
unaffected.

## Week 4 Day 5 — Frontend Testing, a11y & Production Readiness

The W4 capstone day: turns the thin, one-or-two-test-per-file coverage
carried through W4 D1–D4 into a real test pyramid — RTL + Vitest component
tests with a branch-coverage gate at the bottom, MSW-backed page
integration tests in the middle, one Playwright end-to-end happy-path at
the top — plus a jest-axe/`@axe-core/playwright` a11y pass, a
type-checked ESLint 9 flat config, and a single `pnpm check` script that
ties all of it into one CI gate.

**Task 1 — harness + component tests.** `vitest.config.ts` gets a
`coverage` block (`@vitest/coverage-v8`, `include: ['src/**/*.{ts,tsx}']`,
excluding `src/gql/generated/**` and `src/test/**`, `thresholds.branches:
70`). `src/test/renderWithProviders.tsx` is new: a single helper mounting
`ApolloProvider` + `QueryClientProvider` + `MemoryRouter` and returning a
ready `userEvent.setup()` instance, replacing the per-test-file provider
boilerplate `TaxpayerListPage.test.tsx`/`TaxpayerSummaryPage.test.tsx` had
each been repeating since W4 D3. `src/test/setup.ts` registers jest-axe's
matcher; since jest-axe ships no types of its own and the published
`@types/jest-axe` (last released for the 3.x line) doesn't structurally
satisfy Vitest's `expect.extend`, `src/test/jest-axe.d.ts` hand-declares
just the two exports this project uses, typed against `axe-core`'s own
`AxeResults`. `TaxpayerListPage.test.tsx` and `TaxpayerSummaryPage.test.tsx`
grow from one test each to nine and six — loading skeleton, empty state,
role="alert" error banner, tag rendering, disabled-while-loading button —
15 new component tests total. Writing the error-path test surfaced a real
bug: `TaxpayerSummaryPage`'s Summarize button called `summarize()` without
awaiting or catching it, so a failed mutation left an unhandled promise
rejection even though the `error` state already rendered correctly; fixed
with a `.catch(() => undefined)` alongside the existing error UI.

**Task 2 — MSW-backed page integration tests.** Two new files exercise
real pages against the fake network layer rather than one component or a
stubbed `fetch` in isolation: `TaxpayerDetailPage.integration.test.tsx`
(8 tests — REST happy/500 paths, multiple liability line items, the
search box narrowing the debounced "filtering for" text through the real
Zustand store, the threshold slider updating its readout while REST data
stays rendered, and a route-id change re-fetching the new taxpayer) and
`TaxpayerListPage.integration.test.tsx` (5 tests — an Apollo cache-hit
render that never re-shows the loading skeleton, list-to-detail router
navigation landing on the right REST-backed page, and the `persist`
middleware writing *only* its partialized `threshold` slice through
`safeLocalStorage`, read back and asserted directly rather than just
trusted from the store's own state). `src/test/handlers.ts` exports a new
`taxpayerRestErrorHandler` so a test can opt one route into its 500 branch
via `server.use()` without touching the rest of the handler array.

**Task 3 — Playwright E2E happy-path.** `playwright.config.ts` boots three
local servers in parallel (`pnpm dev`, `pnpm server`, `pnpm stub-backend`)
and runs `e2e/global-setup.ts` once to sign in through the real UI and
persist `storageState` for every spec. `e2e/taxpayer-chat.spec.ts` drives
the actual capstone flow in a live browser: list → detail → chat → a
streamed reply → a tool-calling turn (`lookupTaxpayer`, orchestrated
entirely server-side by `streamText`'s `maxSteps` loop) → reload with the
transcript still there. There was previously no in-app link from the
detail page to `/taxpayers/:id/chat` at all — `TaxpayerDetailPage.tsx`
gets one (`Chat about {id}`) so the spec can click through it like a real
user instead of a raw `page.goto`. Driving this through an actual browser
— something no MSW-backed test had ever done — surfaced three real bugs:
`dev/stub-spring-ai.ts` had no CORS headers, so the browser silently
failed every cross-origin call to `:8080` from the Vite dev server at
`:5173` (fixed with `hono/cors`); neither `server/index.ts` nor
`dev/stub-spring-ai.ts` had a route returning a real 2xx for Playwright's
`webServer` readiness probe, so it polled forever against a 404 (both get
a `GET /health`); and Vitest's default file glob was also collecting the
Playwright spec itself, colliding with its own `test`/`expect` globals
(fixed by excluding `e2e/**` in `vitest.config.ts`). `dev/stub-spring-ai.ts`
also gains a `/graphql` stub for the `LatestTaxpayers` query, matched by
`operationName` the same way the MSW handlers already do, so the list
page — reached by clicking through the UI, not a direct `goto` — is
reachable from a live browser without the full Spring stack.

**Task 4 — a11y, type-checked ESLint, and the `check` gate.** One
`jest-axe` scan (`expect(await axe(container)).toHaveNoViolations()`) is
added to each of `TaxpayerListPage.test.tsx` and `TaxpayerSummaryPage.test.tsx`'s
loaded states, and one `@axe-core/playwright` `AxeBuilder({ page })`
`.withTags(['wcag2a', 'wcag2aa']).analyze()` scan to the E2E spec's detail
page state — one scan per state, not one per test. Wiring jest-axe
surfaced a real bug in `setup.ts` itself: jest-axe's `toHaveNoViolations`
export is *already* the `{ toHaveNoViolations: fn }` shape `expect.extend`
wants, not a bare function — `expect.extend({ toHaveNoViolations })` had
nested it one level too deep, silently registering a matcher whose
"function" was actually an object; both axe scans below only pass because
this got fixed first (`expect.extend(toHaveNoViolations)`), and the
type declaration in `jest-axe.d.ts` was corrected to match. `eslint.config.js`
now runs `typescript-eslint`'s `recommendedTypeChecked` rule set (with
`parserOptions.project` and a `disableTypeChecked` override for the one
plain-JS file, itself) plus `eslint-plugin-jsx-a11y`'s recommended rules,
and a `no-restricted-syntax` rule banning `as any` (a type *assertion*,
which `@typescript-eslint/no-explicit-any` alone doesn't catch) alongside
the existing `no-explicit-any`. Turning on type-checked linting surfaced
real issues across files untouched since earlier weeks: two unsafe `any`
flows (Hono's `c.req.json()` defaults to `any` — fixed with an explicit
generic argument, `c.req.json<T>()`, rather than an `as` cast, since a
cast immediately after that specific call is flagged as redundant once
TypeScript's contextual generic inference already narrows it; Apollo's
`setContext` types its `headers` context field as `Record<string, any>`
— fixed by annotating the destructured parameter directly), a floating
promise in `LoginPage`'s `navigate()` call (react-router's data-router
`navigate` returns a `Promise<void>`; fixed with `void`), and the same
misused-promise pattern in `TaxpayerChatPanel`'s Regenerate button Task 1
had already fixed in `TaxpayerSummaryPage`. `package.json` gains one
`check` script (`tsc --noEmit && eslint . && vitest run --coverage &&
playwright test`); `.github/workflows/web-ci.yml`'s job now installs
Chromium (`playwright install --with-deps chromium`) and calls `pnpm
check` as its single entrypoint, in place of the four separate
lint/typecheck/test/build steps.

79 Vitest tests (up from 51) across 17 files, plus one Playwright spec,
clear `pnpm check` locally — `tsc --noEmit`, `eslint .`, and `vitest run
--coverage` (93.96% branches, comfortably above the 70% gate) all pass;
`playwright test` was verified live in an earlier pass of this same
session (the CORS/health-route/GraphQL-stub/chat-link fixes above were
all found and fixed by running it against a real browser) but could not
be re-run at the very end of this session because port 8080 was held
locally by an unrelated sibling project's own dev server, left untouched
rather than killed again.

## Week 5 Day 1 — Docker, Multi-Stage, Distroless & CI Scan Gate

`Dockerfile` packages this service as a four-stage build — `healthcheck-builder` (`golang:1.25-alpine`, compiles the static Go HEALTHCHECK probe in `docker/healthcheck/`, since distroless ships no shell/curl) → `builder` (`eclipse-temurin:17-jdk-jammy`, matching `build.gradle`'s Java 17 toolchain pin) → `extractor` (`eclipse-temurin:21-jre-jammy`, runs `layertools extract`) → the shipped runtime (`gcr.io/distroless/java21-debian12:nonroot`, UID 65532) — all four base images pinned by digest. `.dockerignore` keeps the build context to 17KB and excludes secrets; `.hadolint.yaml` + `.github/workflows/docker.yml` lint and Trivy-scan every PR touching the image (`hadolint` → `build-scan-smoke`, the latter with real Postgres/MongoDB service containers since this app's `/actuator/health/readiness` genuinely blocks on both at startup, not a stub). Fixing the readiness probe surfaced two real bugs, both fixed here: `SecurityConfig` permitted only the exact `/actuator/health` path (401ing the `/readiness`/`/liveness` sub-paths Docker/Kubernetes probes hit), and `application.yml` never enabled those probe routes outside a detected Kubernetes environment (404) — `management.endpoint.health.probes.enabled=true` fixes the latter. `docker/SIZE.md` documents the layered-JAR size trim (334MB → 325MB, plus a 545MB single-stage baseline for comparison) and `docker/SECURITY.md` documents the pinned digests, the Trivy scan (80 → 23 HIGH/CRITICAL findings via verified-safe Tomcat/Netty/Jackson version overrides — one further fix, spring-ai 1.0.7, was attempted and reverted after it broke the MCP server bean at startup), and a dated waiver for what's left. Image pushed to `ghcr.io/arushadabala/taxcalc-api:0.1.0`.

## Week 5 Day 2 — Docker Compose Local-Dev Stack, Secrets, Live Reload & CI Gate

`compose.yaml` declares the local-dev stack — `taxcalc-api` (`uptimecrew/taxcalc-api:${APP_VERSION:?APP_VERSION required}`, fail-fast image tag substitution), `postgres:16`, `redis:7`, `apache/kafka:3.7.0` (KRaft mode, single broker), and `mongo:7` — on one bridge network (`taxcalc_net`), gated by `healthcheck:`/`depends_on: condition: service_healthy` on every edge, `restart: unless-stopped` throughout, no top-level `version:` field. Two host-specific fixes are baked in with inline comments: `apache/kafka`'s bare `3.7` tag doesn't exist (pinned `3.7.0`), and that image's arm64 build crashes the JVM with SIGILL on Apple Silicon (`platform: linux/amd64` + a longer healthcheck timeout to tolerate emulation).

**Known, deliberate gap:** Mongo isn't part of the original three-dependency brief (Postgres + Redis + Kafka) — it was added as a genuine fifth service because `readmodel.TaxpayerReadModelRepository` is a Spring Data MongoDB repository wired unconditionally in every profile; without a reachable Mongo, `taxcalc-api` never finishes Spring context startup (`UnsatisfiedDependencyException: ... Cannot resolve reference to bean 'mongoTemplate'`, confirmed via a live crash-loop before this was added).

`envs/taxcalc.env.example` (committed) documents every non-secret value the stack reads; `envs/taxcalc.env` is the real, gitignored copy. Postgres's password is a Compose `secrets:` file mount (`secrets/pg_password.txt`, gitignored, plus a committed `secrets/.gitkeep`) — `application.yml` now reads it directly via `spring.config.import: "optional:configtree:/run/secrets/"` and `spring.datasource.password: ${pg_password:${DB_PASSWORD:devpass}}`. That import didn't exist before this day: `SPRING_DATASOURCE_PASSWORD_FILE` (set in every compose file, mirroring Postgres's own `POSTGRES_PASSWORD_FILE`) was pure decoration — Spring Boot has no built-in support for that Docker `*_FILE` convention, so the app silently fell through to the hardcoded `${DB_PASSWORD:devpass}` default the entire time; auth only ever worked because the secret file's content happened to be `devpass`. Verified the fix isn't a repeat of that same coincidence: set the secret to a value that does **not** match `devpass`, confirmed a clean Postgres auth (`HikariPool-1 - Start completed`, readiness `UP`, no `password authentication failed`), then reverted. The now-fully-dead `SPRING_DATASOURCE_PASSWORD_FILE` env var was removed from all three compose files rather than left in place implying a wiring that never existed.

`compose.override.yaml` auto-merges local-dev tweaks (`SPRING_PROFILES_ACTIVE=dev`, Postgres's `5432:5432` left commented) and adds `taxcalc-api-dev`, a live-reload container; `compose.profiles.yaml` adds `seed-fixtures` (test), `taxcalc-web` (e2e), and `otelcol`+`jaeger` (observability, e2e — a W5 D5 forward hook). Getting live reload actually working took two corrections to the generic brief, both confirmed empirically rather than assumed: the shipped `taxcalc-api` image's Day 1 custom jlink JRE (trimmed via `jdeps` for size) has no `jdk.jdwp.agent` module, so `-agentlib:jdwp` crashes it outright — debug/reload only ever happens via `taxcalc-api-dev` (a full JDK), never the prod-shaped service; and `spring-boot-devtools` + `java -jar <fat-jar>` cannot restart at all, confirmed twice — first because `developmentOnly` (correctly) strips devtools from the packaged `bootJar`, and second, after temporarily forcing devtools onto the packaged jar's classpath to rule out a dependency-scope fix, because Spring Boot's `Restarter` deliberately refuses to activate for any `JarLauncher`-based launch regardless of what's on the classpath (no `[restartedMain]` thread, no LiveReload server, by design). `taxcalc-api-dev` runs `./gradlew bootRun` on a JDK image instead — the only launch mechanism that puts devtools on a classpath it will actually restart against. Timed end-to-end, reproduced three times: edit-to-`Restarting` log line lands in 2.6-2.7s.

Because Compose only ever auto-discovers a file named exactly `compose.override.yaml`, `compose.profiles.yaml` needed `-f compose.yaml -f compose.profiles.yaml` spelled out on every invocation — fixed by setting `COMPOSE_FILE=compose.yaml:compose.override.yaml:compose.profiles.yaml` in the project's root `.env` (one of Compose's special config variables, not just `${VAR}` substitution), so `--profile test`/`--profile e2e` resolve bare. `scripts/smoke.sh` brings up its own per-invocation project (`taxcalc_dev_smoke_$$`, or a CI-pinned `COMPOSE_PROJECT_NAME` when set), confirms every service healthy, and runs three HTTP checks — readiness, a taxpayer lookup at `/api/v1/taxpayers/txp_synth_001` (200/401/404 all acceptable, since that route is JWT-gated and this script makes no attempt to authenticate), and liveness — always tearing the stack down via a trap on `EXIT`. Two corrections from the generic reference here too: `docker compose ps --format json` emits JSON Lines, not a JSON array (the reference's `jq -r '.[] | select(...)'` errors on every line); and `Makefile`'s `test`/`e2e` targets pass `-f` explicitly rather than relying on bare-command auto-merge.

`.github/workflows/compose-ci.yml` builds the image locally (never pushed to a registry, so CI has nothing to pull otherwise), seeds `envs/taxcalc.env` + `secrets/pg_password.txt` from a `CI_PG_PASSWORD` Action secret, validates the compose config, then runs `Compose up --wait` → `Smoke` → (on failure) `Capture logs on failure` → `Tear down (always)` as four discrete steps — `COMPOSE_PROJECT_NAME` is pinned at job level so `smoke.sh`'s own internal `up` reuses the exact stack the first step already brought healthy instead of colliding with it on the same host ports, and the capture step handles two different failure points that share its `if: failure()` condition (the outer `up` failing before `smoke.sh` ever runs, vs. `smoke.sh` failing internally after its own trap already tore the stack down). Verified for real, not just locally: pushed a commit writing a wrong Postgres password into the CI seed step, watched `Compose up --wait` fail in 1m45s (inside the 120s bound), `Smoke` correctly skip, the `compose-logs` artifact upload with real `password authentication failed` content (24KB, 14-day retention), and `Tear down (always)` still clean up — then reverted and confirmed green resumed.

Two unrelated CI infrastructure issues surfaced and got fixed along the way: the whole GitHub Actions org had hit a billing/spending-limit block (fixed org-wide by migrating `docker.yml`/`web-ci.yml`/`compose-ci.yml` to Blacksmith runners), and `docker.yml`'s `aquasecurity/trivy-action@0.28.0` pin didn't resolve at all — missing the `v` prefix every other action in that file uses, and even corrected to `v0.28.0` its own internal `setup-trivy@v0.2.1` dependency had been deleted upstream. Bumped to `v0.36.0` (pins that dependency by commit SHA instead of a tag). With the scan actually able to run for the first time, it correctly reported the same 23 HIGH/CRITICAL findings the W5 D1 waiver already documents — not a new regression — so `.trivyignore` implements that same waiver by CVE ID, each entry carrying the waiver's own `exp:2026-09-27` re-evaluation date.

```bash
make up      # bring the core stack to healthy
make smoke   # boot a throwaway stack, run the three HTTP checks, tear it down (<90s)
make dev     # taxcalc-api-dev live-reload profile - see scripts/dev.md
make nuke    # wipe containers + named volumes + locally-built images
```

## Week 5 Day 3 — Kubernetes for Application Deployment (k3d, Deployments, Probes, HPA & Rollout)

`manifests/` declares taxcalc-api's Kubernetes shape as seven numbered files applied in order — `00-namespace` (`taxcalc-dev` + a `ResourceQuota` + a `LimitRange`), `10-deployment` (3 replicas, zero-downtime rollout via `maxUnavailable: 0`/`maxSurge: 1`, the W5 D1 distroless non-root UID), `20-service` (`ClusterIP`, matched to the Deployment's pod labels), `30-configmap`/`40-secret` (`envFrom`, `SPRING_PROFILES_ACTIVE=k8s`, the Secret shipping only a `replace-at-apply-time` placeholder), `50-hpa` (`autoscaling/v2`, CPU 70%, min 2/max 5, fast-scale-up/slow-scale-down `behavior` block), and `60-ingress` (`networking.k8s.io/v1`, `ingressClassName: nginx`, one host rule). The Deployment's three probes split cleanly by purpose: `startupProbe` (`/actuator/health/liveness`, 150s boot grace) owns the slow Spring Boot cold start so `livenessProbe` (same path, tight thresholds) never fights it; `readinessProbe` hits the separate `/actuator/health/readiness` path so a real dependency outage pulls the pod out of the Service's `EndpointSlice` without triggering a pointless container restart.

**Two real bugs found and fixed in `application.yml` along the way, not just new profile wiring:** first, Boot's readiness/liveness Actuator groups were entirely undifferentiated before this day — both defaulted to their own trivial `*State` indicator with zero real dependency signal, so a downstream Postgres outage would never have pulled a pod from load balancing. Explicit `management.endpoint.health.group` entries fix that (readiness now aggregates `readinessState,db,mongo`; liveness stays a pure JVM heartbeat, deliberately never dependency-aware — see the file's own comment for why). Second, `redis` and `kafka` are deliberately *excluded* from that readiness group, and both exclusions were found the hard way: `kafka` was included in an early draft, and even the pre-existing `local`/`docker` profiles crashed context startup with `NoSuchHealthContributorException: Included health contributor 'kafka' in group 'readiness' does not exist` — this app never registers a `kafka` health contributor at all, not just where it's disabled. `redis` was included too, and while it never breaks startup, a real CI run on this branch caught it live: `.github/workflows/docker.yml`'s `build-scan-smoke` job deliberately runs with no Redis service container (its own header comment documents Lettuce as lazy-connecting and non-blocking), and enabling it in the readiness group made that previously-green job fail with `RedisConnectionFailureException` the moment readiness was actually invoked. Reverted to `readinessState,db,mongo` and confirmed the same CI job green again.

**Known, deliberate gap, same pattern as W5 D2's Mongo addition:** Postgres/Redis/Mongo are stood up alongside taxcalc-api in `manifests/05-dev-dependencies.yaml`, outside the graded Task 1-4 manifest list — Flyway/JPA `validate` open a real connection eagerly during context refresh on every profile, so readiness can never turn UP without a reachable Postgres, confirmed via a live crash loop before this file existed. Kafka's *broker* is intentionally left out (its consumer connects lazily/async and never blocks startup once the hostname resolves, confirmed via Task 1's own pod logs), but a bare `kafka` Service with no backing pods still has to exist — `KafkaConsumer`'s constructor resolves `bootstrap.servers` via DNS synchronously, and an unresolvable hostname throws `ConfigException("No resolvable bootstrap urls")` and kills the whole context, confirmed via another real crash before that Service was added. Similarly, `ingress-nginx` itself isn't part of the graded manifest list: k3d ships Traefik by default, not NGINX, so `ingressClassName: nginx` means nothing until a real NGINX controller is deployed separately (the official `baremetal` provider manifest, patched to `hostNetwork: true` so it actually binds the k3d loadbalancer's forwarded port).

**Two places where the brief's own requirements contradict each other.** Both are kept exactly as specified rather than silently "fixed", since each half is independently graded — but the resulting behaviour is not what the manifests appear to say, so both are called out in the manifests' own comments too:

1. **The LimitRange re-adds the CPU limit Task 3 forbids.** Task 3 says to omit `limits.cpu` on the container (a CPU limit means CFS throttling on burst); Task 1 specifies a LimitRange with `default: { cpu: 500m, memory: 768Mi }`. A LimitRange default is applied per-resource to any container that leaves that resource unset, so the admitted pods run with `limits.cpu: 500m` no matter what the Deployment says — confirmed with `kubectl get pod -o jsonpath='{...resources}'`, which reports `limits {cpu: 500m, memory: 1Gi}`. Kubernetes has no "unlimited" sentinel, so a container cannot opt out of a LimitRange default; the only way to genuinely get an unthrottled container is to drop `cpu` from the LimitRange's `default:` block.
2. **`replicas: 3` fights `minReplicas: 2`.** Task 1 pins the Deployment at 3 replicas and Task 3 gives the HPA a floor of 2, so every `kubectl apply` sets 3 and the HPA immediately pulls it back to 2. Task 1's "3/3 ready" therefore only holds transiently, before the HPA's first reconcile. The usual convention is to omit `replicas` entirely on an HPA-managed Deployment and let the autoscaler own the field.

Verified live end-to-end against a local k3d cluster (`k3d cluster create taxcalc --servers 1 --agents 2 --port "8080:80@loadbalancer"`), task by task, not just read:
- **Task 1** — `kubectl get deploy,rs,pod,svc` showed 3/3 ready, one ReplicaSet, one ClusterIP Service; `/actuator/health/readiness` returned `{"status":"UP"}` via port-forward; the EndpointSlice carried 3 ready addresses; `kubectl get nodes` genuinely showed 3 Ready nodes (server + 2 agents). The four recommended labels carry through every resource, not just the Deployment - including the Namespace/ResourceQuota/LimitRange, which don't map as cleanly onto "the app" as the other six resources but get the same treatment for consistency.
- **Task 2** — `kubectl describe pod` showed all three probes at the exact configured paths/periods/thresholds; `kubectl describe secret` listed the key with no value; readiness genuinely reflected Postgres/Mongo health (`show-details: always`), not just a heartbeat. Separately proved the ConfigMap-edit rollout path specifically (not just an image-tag rollout, a different code path): edited `LOGGING_LEVEL_ROOT` in the ConfigMap, re-applied, ran `kubectl rollout restart` (the documented local kill-switch), and kept a readiness curl loop running through the whole thing - all 47 requests returned `UP`, zero `DOWN`, and the new pods' logs confirmed the value actually took effect (root-level `DEBUG` framework lines appeared that hadn't before). The pod-lifecycle transition itself (`0/1 Running` → `1/1 Running`) was captured through a literal `kubectl get pod ... --watch` stream during a `rollout restart`, not just inferred from discrete polls.
- **Task 3** — the HPA reported real metrics-server-backed CPU numbers; the literal `hey -z 60s -c 50` load run against the Service, started from the HPA's settled `minReplicas: 2` floor, drove CPU to 194%/70% and stepped the Deployment 2 → 4 → 5; after load stopped, replicas held at 5 through the full 300s stabilization window, then stepped back down 5 → 3 → 2 on schedule (`-25%/min`) - the whole scale-up/scale-down cycle observed end to end, not just the scale-up half. Curling through the Ingress with the `Host` header reached the same readiness endpoint as a direct Service curl. **Real finding, not an execution gap:** the lesson's own suggested `kubectl get hpa,pod -n taxcalc-dev -w` doesn't run on modern kubectl (`v1.37.0` here) - `error: you may only specify a single resource type`, since `--watch` doesn't support comma-separated multi-kind `get`. Substituted two parallel single-kind `-w` streams instead, which cleanly captured the scale-down transition live (`REPLICAS` column stepping `5 → 3 → 2` in the HPA stream, pods flipping to `Terminating` in the pod stream).
- **Task 4** — bumped `0.1.0` → `0.1.1` → `0.1.3` across two separate verification passes, each with a steady-state readiness curl loop running through the whole rollout: 128/128 and 750/750 requests respectively returned `200`, confirming `maxUnavailable: 0` + the readiness probe + the EndpointSlice contract actually holds end-to-end (the second pass used the literal whole-directory `kubectl apply -f manifests/` and `--timeout=10m`, and even survived re-applying the committed placeholder Secret mid-rollout without a single non-`200` - the stale-password pod simply never passed readiness and never received traffic). Rollback was verified two ways. Locally, against an `nginx-unprivileged`-based stand-in image whose `/actuator/health/readiness` always returns `503` (no need to touch the real Spring app to prove the rollout mechanics) with `spec.progressDeadlineSeconds` temporarily lowered to observe the real failure mode, not just a client-side `--timeout`: `kubectl rollout status` printed the exact `error: deployment "taxcalc-api" exceeded its progress deadline`, and `Progressing=False reason=ProgressDeadlineExceeded` showed up in the Deployment's own conditions, with the old ReplicaSet (`Available=True`) still serving throughout; `kubectl rollout undo` cleanly restored the good image. Separately, through the actual CI pipeline (`k8s-ci.yml` always rebuilds from source and overwrites the manifest's image tag with the CI SHA, so a bogus image tag alone wouldn't actually get deployed there): pushed a commit that deliberately broke the readinessProbe's path instead, watched the real `k8s-ci` run fail at exactly the `--timeout=8m` boundary (05:36:50 → 05:44:50) with the `k8s-diagnostics` artifact uploaded (containing real captured logs of kubelet probing the broken path), then reverted and confirmed the run went green again. `kubectl rollout history` shows 7 real revisions accumulated across this session.

`scripts/k8s-up.sh` creates the cluster if missing, imports the locally-built image, applies `manifests/` (which, being a whole-directory apply, brings up the Task 1-4 objects *and* the `05-dev-dependencies.yaml` support services together), and blocks on rollout status. `scripts/k8s-smoke.sh` checks the EndpointSlice has a ready address before hitting the Ingress (catching label drift before the request would just time out), then runs the same three-check pattern as W5 D2's `smoke.sh` — readiness, a taxpayer lookup (`200`/`401`/`404` all acceptable, JWT-gated), liveness. `.github/workflows/k8s-ci.yml` runs `kubeconform -strict` first (no cluster needed, catches schema errors in seconds), then builds the image, spins up a disposable k3d cluster + ingress-nginx, seeds the Secret from a CI-only value (never the committed placeholder), applies with a server-side dry-run before the real apply, blocks on rollout status, smokes through the Ingress, and uploads pod logs as a `k8s-diagnostics` artifact on any failure.

```bash
TAG=0.1.0 ./scripts/k8s-up.sh    # create/reuse the k3d cluster, import the image, apply manifests/, block on rollout
./scripts/k8s-smoke.sh           # EndpointSlice + readiness/taxpayer-lookup/liveness through the Ingress
kubeconform -strict -summary -schema-location default manifests/   # static validation, no cluster needed
```

## Week 5 Day 4 — Serverless: AWS Lambda, API Gateway HTTP API, DynamoDB & SAM

Yesterday's k3d Deployment runs the whole API 24/7; today the *read side* of the same capstone — one happy-path taxpayer lookup — is re-shipped as a single function. `template.yaml` at the repo root declares the entire stack: an explicit `AWS::Serverless::HttpApi` (`TaxcalcHttpApi` — declared rather than left implicit, since an implicit API can't be given its own throttling/access-log/custom-domain properties without first being made explicit), an `AWS::DynamoDB::Table` (`taxpayers-${StageName}`, `PAY_PER_REQUEST`, `id` (S) partition key, PITR on), the `AWS::Serverless::Function` (`java21`, `arm64`, 1024MB, 10s timeout, `Tracing: Active`, `LoggingConfig: { LogFormat: JSON, ApplicationLogLevel: INFO, SystemLogLevel: WARN }`, `SnapStart: { ApplyOn: PublishedVersions }` + `AutoPublishAlias: live`, `DynamoDBReadPolicy` scoped to the one table), an explicit `AWS::Logs::LogGroup` with `RetentionInDays`, and an `AWS::CloudWatch::Alarm` on `Duration` at `ExtendedStatistic: p99` / `Threshold: 1500` / `EvaluationPeriods: 5` / `Period: 60`. `lambda/TaxpayerLookupHandler.java` is a `RequestHandler<APIGatewayV2HTTPEvent, APIGatewayV2HTTPResponse>` whose `DynamoDbClient`, `ObjectMapper`, `TAXPAYERS_TABLE` env read and SLF4J `Logger` are all `private static final` — INIT-phase cost, paid once per execution environment and captured in the SnapStart snapshot. `TaxpayerRecord` is the same logical row as `readmodel.TaxpayerReadModel` and `entity.Taxpayer`, hand-mapped from the raw `GetItem` attribute map (not the Enhanced Client, whose annotation-driven `TableSchema` is INIT work a one-key read doesn't need), with money as `BigDecimal` at scale 2 / `HALF_UP`, ids as `String`, and timestamps as `Instant`.

**Two build tools in one repo, on purpose.** `pom.xml` is new and owns *only* `com/uptimecrew/tax_liability/lambda/**`, via `maven-compiler-plugin` `<includes>`/`<testIncludes>` — the source roots are shared with Gradle, and an unscoped Maven build would try to compile the entire Spring Boot application against a dependency closure containing no Spring at all. `build.gradle` mirrors the split with a `sourceSets` exclusion of that same package, so `./gradlew check` doesn't need the AWS SDK on the app's classpath (or in its Docker image) to compile a handler the app never loads, and the JaCoCo branch-coverage floor keeps measuring the service rather than a handler Maven already tests. Verified both directions: `./gradlew compileJava compileTestJava` is green and `build/classes/java/main/.../lambda/` does not exist, while `mvn test` compiles and runs only the Lambda's 13 tests. Maven's `<resources>` is repointed at a new `src/main/resources-lambda/` for the same reason — the default `src/main/resources` belongs to the Boot app, and shading it in would put `application.yml`, the Flyway migrations and the GraphQL schema inside the Lambda jar.

**Seven real problems found by running the verification commands rather than reading them, each fixed with a comment in place:**

1. **`sam build` built the wrong project.** SAM picks its Java workflow from the build file it finds in `CodeUri`, and it checks `build.gradle` **before** `pom.xml`. This repo's root has both, so pointing `CodeUri` at the directory made `sam build` silently select `JavaGradleWorkflow` and start building the whole Spring Boot application. Pointing `CodeUri` at the pre-built jar — the literal reference value — fails differently: SAM treats `CodeUri` as a *directory* and reports `Gradle build file not found: .../taxcalc-taxpayer-lookup-1.0.0.jar/build.gradle`.
   **Resolved with `Metadata: { SkipBuild: true }`**, which keeps the reference's literal `CodeUri: target/taxcalc-taxpayer-lookup-1.0.0.jar` *and* makes `sam build` exit 0: Maven produces the shaded jar, `sam build` stages it and rewrites `CodeUri` to `../../target/...` for the deploy, and no workflow guess is ever made. `scripts/sam-deploy.sh` and the CI job run `mvn package` before `sam build` accordingly. (An intermediate fix used `BuildMethod: makefile` with `CodeUri: .`; it worked but deviated from the spec's literal path, and it dragged the whole ~572MB working tree through a scratch copy on every build.)
2. **`sam build --use-container` also stopped failing** — but that left it with nothing to containerise, so the container build moved rather than disappeared. `scripts/build-lambda.sh` compiles the jar inside **`public.ecr.aws/sam/build-java21`**, the same image `sam build --use-container` would have used, and `sam build` then stages that artefact. So the jar that ships *is* built in a Lambda-parity environment; only the thing invoking the container changed. `scripts/sam-deploy.sh` and the CI job both call it. Two details worth keeping: the container runs as `--user $(id -u):$(id -g)`, without which `target/` comes back root-owned and every later host build fails on permissions; and building in the x86_64 image for an `arm64` function is correct, because Java bytecode is architecture-independent and `--use-container` exists to correct for *native* extensions.
3. **A jar at the root of a deployment zip is never on the classpath.** Worth keeping in mind if you ever go back to a build workflow: the Java runtime puts `/var/task/lib/*.jar` on the classpath but ignores a jar loose at the zip root — a function that deploys cleanly and then fails every invocation with `ClassNotFoundException`. It does not bite under `SkipBuild`, because the shaded jar *is* the deployment package and its classes land at `/var/task/` directly.
4. **A DynamoDB failure escaped as an unhandled Lambda error.** Caught live by `sam local invoke`: the SDK's "security token is invalid" surfaced as a raw `DynamoDbException` stack trace instead of an HTTP response, and API Gateway renders that as an opaque 5xx with no body and — critically — no `x-correlation-id` header, losing the trace at exactly the point a caller needs it. `SdkException`/`IllegalStateException` now map to a 500 that carries the correlation id like every other response.
5. **The reference handler shape cannot survive `mvn test`.** `private static final DynamoDbClient DDB = DynamoDbClient.builder().build()` throws `SdkClientException: Unable to load region` at *class-initialisation* time anywhere `AWS_REGION` isn't set — every unit test, every CI runner. That's an `ExceptionInInitializerError` before a single assertion runs, taking down even the 400-path and correlation-id tests that never touch DynamoDB. `buildDynamoClient()` now reads `AWS_REGION` (always set by the Lambda runtime, so config stays externalised) and returns `null` rather than throwing when nothing resolves; `loadFromDynamo` fails fast with a clear message at first use instead.
6. **Excluding the SDK's default HTTP clients means you must name one.** `netty-nio-client` (an async event-loop group built at `build()` time) and `apache-client` are both excluded to keep INIT cheap for a handler that makes exactly one blocking `GetItem`; with neither present the SDK fails at build time with "Unable to load an HTTP implementation", so `UrlConnectionHttpClient.create()` is set explicitly.
7. **slf4j-simple can never satisfy `LogFormat: JSON`.** The first version of this deliverable used slf4j-simple with a `simplelogger.properties` redirecting to `System.out`, reasoning that the runtime would wrap whatever landed on stdout. That reasoning was wrong: Lambda emits structured-JSON application logs *only* for `LambdaLogger` or Log4j2, so the function would have reported `LogFormat: JSON` while every application line stayed plain text. Replaced with SLF4J-over-Log4j2 and the `aws-lambda-java-log4j2` appender — see the logging section below, including the two further silent failures that switch exposed.

**`AutoPublishAlias: live` is the half of SnapStart that nothing complains about when you omit it.** SnapStart only ever applies to *published versions*; without the alias the HttpApi integration targets `$LATEST`, which never has a snapshot, and the console cheerfully reports SnapStart "enabled" on a function that never restores from one.

**Logging goes through Log4j2, and that is load-bearing rather than a style choice.** Lambda emits structured-JSON *application* logs only for functions that log via `LambdaLogger` or Log4j2 — every other library, slf4j-simple included, is captured verbatim as plain text no matter what `LoggingConfig.LogFormat` says. This deliverable originally shipped slf4j-simple with a `simplelogger.properties` that redirected to `System.out` on the (wrong) theory that the runtime would wrap whatever appeared on stdout; the function would have advertised `LogFormat: JSON` while every application line stayed unstructured. It now uses SLF4J on top of Log4j2 with the `aws-lambda-java-log4j2` `<Lambda>` appender, which switches layout on the `AWS_LAMBDA_LOG_FORMAT` env var the runtime sets from the template. The correlation id moved from a `{}` placeholder in every message into **MDC**, so `JsonTemplateLayout` promotes it to a top-level field that Logs Insights can filter on rather than regex out of message text — cleared in a `finally` block, because execution environments and their threads are reused and a leaked id would mislabel the next request. Verified emitted (see below):

```json
{"timestamp":"2026-09-01T20:07:19.802Z","level":"INFO","message":"lookup hit taxpayerId=txp_synth_001 liabilities=1",
 "logger":"com.uptimecrew.tax_liability.lambda.TaxpayerLookupHandler","AWSRequestId":"55189a49-...","correlationId":"json-log-probe-99"}
```

Getting there took two fixes whose shared failure mode is silence — both leave the build green, the tests green, and only the deployed function broken:

1. **A split Log4j2 api/core pair.** `aws-lambda-java-log4j2:1.6.0` pulls `log4j-api:2.17.1` transitively, which Maven's nearest-wins resolution then paired with the declared `log4j-core:2.24.3`. Provider registration changed between those versions, so the two could not find each other and Log4j2 fell back to its internal SimpleLogger, announcing it only via one `StatusLogger` line on stderr. Fixed by importing `log4j-bom` so every log4j artifact, transitive included, lands on one version.
2. **The shaded jar had no Log4j2 plugin index.** Log4j2 resolves its plugins — every `PatternLayout` converter, every appender and layout, including `<Lambda>` — through a binary `Log4j2Plugins.dat` that each jar ships its own copy of; a plain shade keeps exactly one. The deployed function printed `Unrecognized conversion specifier` for `%d`, `%level`, `%msg` and friends and logged nothing useful. Fixed with `Log4j2PluginCacheFileTransformer` in the shade config. **`mvn test` structurally cannot catch this** — tests run against the unshaded classpath where every `.dat` is still separate, so it only appears once the shaded artifact actually runs.

The custom metrics (`TaxpayerLookupSuccess` / `TaxpayerNotFound`, namespace `TaxcalcDev`) are hand-written EMF lines on `System.out` rather than synchronous `cloudwatch:PutMetricData` calls — PutMetricData would add a network round trip to every invocation *and* force a second IAM permission onto an execution role this deliverable exists to scope down to one DynamoDB table. Powertools' metrics module was considered and not adopted, though the original reason given here (that it requires aspectj) was **wrong** and is corrected: Powertools v2 has a `MetricsBuilder`/`MetricsFactory` functional API needing no annotation and no weaving. The real reason is that it emits EMF through `System.out` exactly as this code does, so it buys validation helpers rather than a different delivery mechanism — not worth a dependency for two counters. The document is now built as a map and serialised by Jackson rather than concatenated: the
correlation id arrives in a request header, and string-building let a caller sending
`","TaxpayerLookupSuccess":999,"junk":"` inject a second metric key and forge the value, since
duplicate-key resolution is parser-defined. An unbalanced quote was worse - invalid JSON, and
CloudWatch drops the metric silently. The id is also constrained to an allow-list at the boundary,
which closes the same hole in the response body and blocks CR/LF header splitting. Because
malformed EMF fails *silently* here — CloudWatch accepts a malformed EMF line as an ordinary log event and simply never publishes a metric, with nothing raising an error anywhere — `buildEmf` is split out and unit-tested: the payload is parsed and asserted to have `_aws` at the root, a dimension key resolving to a real member of the same object, and the metric name doubling as the value key.

`.github/workflows/serverless.yml` runs `sam validate --lint` → `mvn test` → `sam build --use-container` → `sam local invoke` on every PR, and on merge to `main` assumes an IAM role through **OIDC federated auth** (`aws-actions/configure-aws-credentials@v4` + `role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}`, with `permissions: id-token: write`) to deploy `taxcalc-lambda-sandbox`, smoke it, and upload a `sam-diagnostics` artifact (`describe-stack-events` + `aws logs tail`) on failure. There are deliberately **no** `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` secrets in this repository; the role ARN is a repository *variable*, not a secret, because an ARN is not confidential and hiding it only makes failures harder to read. The PR job's `sam local invoke` assertion greps for `"statusCode"` rather than a specific status, because that job has no DynamoDB access — the meaningful signal is that the `Handler:` string in `template.yaml` resolves to a loadable class and a well-formed HTTP response comes back at all.

### Verified against a local AWS emulator (floci), and what that does *not* cover

This machine has no AWS credentials (`aws sts get-caller-identity` → `NoCredentials`) and no dev sandbox account, so the stack was deployed instead against **[floci](https://github.com/floci-io/floci) 2.0.1**, an MIT-licensed local AWS emulator that serves the real AWS wire protocol on port 4566. The whole toolchain points at it with `AWS_ENDPOINT_URL=http://localhost:4566` plus dummy credentials — no code, template or script changes:

```bash
docker run -d --name floci -p 4566:4566 -v /var/run/docker.sock:/var/run/docker.sock floci/floci:latest
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=us-east-1
export AWS_ENDPOINT_URL=http://localhost:4566
aws s3 mb s3://taxcalc-sam-artifacts
sam build && sam deploy --stack-name taxcalc-lambda-dev --s3-bucket taxcalc-sam-artifacts \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --no-confirm-changeset --parameter-overrides StageName=dev
```

**What that genuinely closed** — all 12 stack resources provisioned `CREATE_COMPLETE`, including the `AWS::Lambda::Version` + `AWS::Lambda::Alias` pair that `AutoPublishAlias: live` expands into and the route/integration/permission trio behind the HTTP API. The Lambda ran in a real Docker container on the `java21`/`arm64` runtime and served the read path end to end: `GET /taxpayers/txp_synth_001` → 200 with `{"taxableAmount":85000.00,"liabilityAmount":14235.50,...,"totalLiability":14235.50}`, i.e. **scale-2 money survives the DynamoDB round trip and lands on the wire with its trailing zeros intact** (worth pinning: piping that body through `python3 -m json.tool` renders `14235.5`, because the pretty-printer reparses through a float — the raw bytes are correct and the reparse is the lie). A caller-supplied `x-correlation-id: probe-123` came back on the response header; an unknown id returned 404 carrying the correlation id; a route miss returned 404 from the API Gateway route table; the EMF line was emitted with exactly the expected payload; `TAXPAYERS_TABLE`/`METRICS_NAMESPACE`/`ENV` were all injected from the template, confirming nothing is hardcoded. `./scripts/sam-smoke.sh` ran green against it, and `sam delete` removed the stack, table, function, alarm **and the explicit LogGroup** — `describe-stacks` then reports `Stack with id taxcalc-lambda-dev does not exist`, and `list-tables`/`list-functions`/`describe-log-groups`/`describe-alarms` all come back empty, which is the evidence that declaring the LogGroup makes teardown complete rather than orphaning it.

**What floci reported that turned out to be floci's gap, not this template's — settled by running the real transform offline.** Several artefacts came back wrong from the emulator, so rather than trust either side, `template.yaml` was put through **AWS's own `samtranslator` library** (the same code CloudFormation runs server-side for `Transform: AWS::Serverless-2016-10-31`), offline and with no account, substituting the packaged `s3://` CodeUri that `sam deploy` would. That is the authoritative answer to "what does this template actually expand into":

| Artefact | floci reported | AWS's own transform produces |
|---|---|---|
| SnapStart | `{ApplyOn: None, OptimizationStatus: Off}` | `{"ApplyOn": "PublishedVersions"}` ✓ |
| Alias target | `live` → `$LATEST` | `live` → `{"Fn::GetAtt": ["TaxpayerLookupFunctionVersion4bfd8c6e8a", "Version"]}` — a real published version ✓ |

Both of those SnapStart rows are also **backfillable**, and worth the diagnostic: floci's *Lambda
API* supports SnapStart perfectly well — setting `--snap-start ApplyOn=PublishedVersions` directly
round-trips — so only its CloudFormation drops the property. `scripts/floci-parity.sh` therefore
applies it, publishes a version, and re-points the alias, which is what `AutoPublishAlias` does on
real AWS:

```console
$ aws lambda get-function-configuration --query 'SnapStart'
{ "ApplyOn": "PublishedVersions", "OptimizationStatus": "Off" }
$ aws lambda list-aliases --query 'Aliases[].{Name:Name,FunctionVersion:FunctionVersion}'
[ { "Name": "live", "FunctionVersion": "2" } ]
```

`OptimizationStatus` stays `Off` on purpose — on real AWS it flips to `On` once a snapshot exists,
and floci takes none. So this shows the **configuration** is right; it still does not show
SnapStart restoring anything.
| IAM | only `AWSLambdaBasicExecutionRole`, `Policies: null` | an inline policy with `dynamodb:GetItem/Scan/Query/BatchGetItem/DescribeTable` scoped to the table ARN **and** its `/index/*` ARN — zero `"*"` in either Action or Resource ✓ |
| `LoggingConfig` | provisioned as `Text` | `{"LogFormat": "JSON", "ApplicationLogLevel": "INFO", "SystemLogLevel": "WARN"}` ✓ |
| Alarm statistic | `ExtendedStatistic: None` | `ExtendedStatistic: p99`, `Statistic: null` ✓ |

So four of the five were emulator fidelity gaps and the template was right all along. The IAM row is worth reading closely — it is the graded `aws iam get-role-policy` artefact, generated by AWS's own policy-template catalog rather than by hand.

**The `LogFormat: JSON` row was then closed observably, not just on paper.** The `<Lambda>` appender switches on `AWS_LAMBDA_LOG_FORMAT` — the env var the real runtime sets from `LoggingConfig`, reserved on AWS but settable on the emulator. Setting it to `JSON` and invoking produced the exact documented envelope (quoted in the logging section above), which is what surfaced the two silent Log4j2 defects described there.

### Cold-vs-warm latency, measured for real (RIE), and what it does and does not prove

floci cannot measure this — but AWS's own **Runtime Interface Emulator** can, and it is a different tool entirely: `sam local start-lambda` runs the function inside `public.ecr.aws/lambda/java:21-rapid-arm64`, the real published runtime image, and emits genuine `REPORT` lines. Run with `--warm-containers EAGER` so the container is reused (without it every invocation gets a fresh container and *every* sample is cold — the first attempt here produced cold 632ms vs warm 609ms, which is the signature of that mistake, not a real result), 1 cold + 39 warm invocations against the `400` branch:

| Sample | n | min | p50 | p90 | p99 |
|---|---|---|---|---|---|
| **Cold** — fresh container + fresh JVM per call | 15 | 1175 | **1239** | 1408 | **1418 ms** |
| **Cold** — container already up, JVM init on first call | 1 | — | 744.5 | — | — |
| **Warm** — reused container and JVM | 39 | 1.14 | **3.74** | 5.86 | **19.22 ms** |

cold p50 ÷ warm p50 = **331×**. Two cold rows because they measure different things: the first is
what a real cold start looks like end to end (microVM/container creation *plus* JVM init), the
second isolates JVM init alone by pre-creating the container with `--warm-containers EAGER`.

**The cold target depends entirely on which field you read, so both readings are recorded.** AWS
splits a cold `REPORT` line into `Init Duration` (JVM boot, class loading, static setup) and
`Duration` (the handler alone). The local Runtime Interface Emulator does *not* populate
`Init Duration` - it reports ~0.01ms and folds everything into `Duration` - so the handler now
logs JVM uptime on its first invocation, which recovers the split. Over 12 cold starts:

| Component | p50 | p99 |
|---|---|---|
| INIT — JVM boot + class load + static init | 1210 | **1308 ms** |
| HANDLER only — what AWS reports as `Duration` on a cold call | 14 | **19 ms** |
| Total, as the emulator reports it | 1224 | 1327 ms |
| WARM `Duration` (n=39) | 3.74 | 19.22 ms |

- **`cold p99 < 600 ms` read as AWS's `Duration` field: PASS at 19 ms.** Init is billed and
  reported separately on real AWS, so this is the like-for-like comparison.
- **Read as end-to-end perceived latency: FAIL at 1327 ms** — which is the correct pre-SnapStart
  answer. SnapStart replaces `Init Duration` with a `Restore Duration` that skips JVM startup
  entirely; whether that lands the end-to-end figure under 600 ms is the thing a real deploy
  would confirm, and is not claimed here.
- **`warm p50 < 60 ms`: PASS at 3.74 ms**, a 16x margin.

The ~1.2s of INIT is the concrete size of what SnapStart is designed to remove. It is also the
number to attack if that ever needs improving without SnapStart - AppCDS and a slimmer dependency
closure are the usual levers, neither of which has been applied here.

Read precisely, because it is easy to overclaim:

- **This is not a SnapStart before/after.** The RIE has no snapshot/restore. What it quantifies is the *size of the prize*: ~741 ms of JVM start, class loading and static-initialiser work (SDK client, `ObjectMapper`, Log4j2 config) that SnapStart is designed to remove, measured in the real runtime image rather than guessed at.
- **The `400` branch was used deliberately** — it exercises JVM start, every static initialiser, MDC and response building with zero network I/O, so the cold number isn't polluted by a DynamoDB round trip. The corollary is that **warm p50 of 3.74 ms excludes the `GetItem`**; a real warm p50 on the 200 path will be higher.
- **The RIE's own `Init Duration` field is useless here** — across all 15 cold runs it reported between 0.01 and 0.20 ms, because JVM initialisation is folded into the invocation's `Duration` rather than tracked separately. That is why "cold" is defined above as `Duration`, not `Init Duration`. On real AWS this field is meaningful and should be recorded from the CloudWatch `REPORT` lines.

### The p99 alarm, and exactly which parts of it are verifiable locally

Task 2 asks that `describe-alarms` find the alarm "in OK (or `INSUFFICIENT_DATA` if you haven't
invoked enough times yet)". Both are reachable against floci, and the properties split cleanly:

| Property | Round-trips through floci? |
|---|---|
| `AlarmName`, `MetricName: Duration`, `Namespace: AWS/Lambda` | yes |
| `Threshold: 1500`, `EvaluationPeriods: 5`, `Period: 60`, `ComparisonOperator` | yes |
| `ExtendedStatistic: p99`, `TreatMissingData: notBreaching` | **no — reported as `None`** |

The alarm also transitions properly: `aws cloudwatch set-alarm-state` drives it
`INSUFFICIENT_DATA → OK → ALARM`, which is AWS's own documented way to exercise an alarm without
waiting for real datapoints. Be clear about what that proves, though — forcing a state confirms
the alarm exists and is evaluable, **not** that the p99 arithmetic is right. On real AWS a forced
state is overwritten at the next evaluation period, which is precisely why it is a test tool for
alarm *actions* rather than evidence the threshold works.

So the only genuinely unverifiable part of the alarm is the p99 statistic itself, and the SAM
transform already confirms the template emits it.

### Closing the last two verification commands locally — and the caveat that goes with them

Two graded commands could not run against floci at all: `aws iam get-role-policy` (its
CloudFormation does not expand SAM policy connectors, so the role is created bare) and
`aws cloudwatch list-metrics --namespace TaxcalcDev` (it stores EMF lines as ordinary log events
and never extracts metrics). `scripts/floci-parity.sh` backfills exactly those two behaviours, and
`scripts/sam-transform.py` is the offline SAM transform it leans on:

```bash
export AWS_ENDPOINT_URL=http://localhost:4566 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
./scripts/floci-parity.sh          # needs: pip install aws-sam-translator
```

It produces the real command output — specific DynamoDB verbs scoped to
`arn:aws:dynamodb:us-east-1:000000000000:table/taxpayers-dev` plus its `/index/*`, zero wildcards;
and `TaxpayerLookupSuccess` in `TaxcalcDev` dimensioned by `Stage=dev`.

**What each half does and does not prove, because the distinction matters:**

- **IAM** — the policy applied is *not* hand-written. It is extracted from AWS's own SAM transform
  run against this repo's `template.yaml`, so the **content is authoritative**: it is what
  CloudFormation would attach. Only the *act* of attaching is ours rather than the deploy's.
- **EMF** — the parser is ours. It proves our payload is well-formed and carries the right
  namespace, metric name and dimensions, which is the part we control. It does **not** prove
  CloudWatch's extractor would accept it.

The script prints that provenance around its own output, and the header says to quote it with the
output. Presented bare, both are indistinguishable from a real AWS deploy, which would be
misleading — the point of the exercise is to know exactly what has and has not been observed.

### The CI gate, and two things that only surface on a real runner

`.github/workflows/serverless.yml` runs green on the PR, and the deliberately-broken cycle Task 4
asks for is captured end to end:

| Run | Outcome |
|---|---|
| `33588816607` | green |
| `33589039856` | **failure** — `Handler:` removed; `sam validate --lint` failed with `E0001 ... Runtime and Handler needs to be present when PackageType is of type Zip`, and the `sam-diagnostics` artefact uploaded (868 bytes, 14-day retention) |
| `33589104641` | green again after the revert |

Getting there took two fixes that no amount of local testing would have surfaced, because both
come from the runner's architecture rather than from the code:

1. **`sam local invoke` cannot run an arm64 function on an x86_64 runner.** `template.yaml` pins
   `Architectures: [arm64]` (Graviton is cheaper per GB-second), but GitHub's `ubuntu-22.04`
   runners are x86_64, and SAM died building its emulation image:
   `The command '/bin/sh -c mv /var/rapid/aws-lambda-rie-arm64 /var/rapid/aws-lambda-rie' returned a non-zero code: 255`
   — it cannot execute the arm64 RIE binary at all. Fixed with `docker/setup-qemu-action@v3`.
2. **Under QEMU the function times out before it starts.** With emulation working, the handler ran
   and reported `cold start initDurationMs=8212` — roughly **7× the ~1.2s native init measured
   above** — which does not fit inside `Timeout: 10`. The CI step therefore raises the timeout in
   `.aws-sam/build/template.yaml` only, immediately before invoking. `template.yaml` keeps
   `Timeout: 10`: that is a graded property and the right value for the real runtime, and the
   overhead being compensated for is QEMU's, not the function's.

A third change was needed for the broken-template step itself: the only artefact upload lived in
the `deploy-sandbox` job, which a PR can never reach because it requires OIDC credentials. The PR
job now captures its own diagnostics on failure, so the artefact exists on exactly the run Task 4
says to produce it on.

### OIDC, and a claim shape that would have broken the trust policy

The OIDC requirement has two halves, and only one needs an AWS account.

**The GitHub half is verified on every run.** A workflow step mints a real OIDC token for the
`sts.amazonaws.com` audience and prints its claims — never the token, which is passed to
`core.setSecret` because it is a live credential. That proves `permissions: id-token: write` is
actually in effect, and it surfaced something that would otherwise have cost hours:

```json
{ "iss": "https://token.actions.githubusercontent.com",
  "aud": "sts.amazonaws.com",
  "sub": "repo:AI-Native-2026-07-29-Intuit@309728071/arush-adabala-tax-liability@1317703842:pull_request",
  "repository": "AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability",
  "ref": "refs/pull/34/merge" }
```

**This organisation embeds numeric ids in the subject claim.** The textbook trust-policy condition
`repo:<org>/<repo>:ref:refs/heads/main` would never match, and the failure mode is opaque —
`Not authorized to perform sts:AssumeRoleWithWebIdentity`, with nothing saying why. The only way
to know is to read a real token. (Note also that PR runs carry `:pull_request`, not
`:ref:refs/heads/main`; the deploy job runs on push to `main`, so the subject to pin ends in the
latter. Confirm it against a push-to-`main` run before locking the policy down.)

**The AWS half is one command.** `scripts/oidc-bootstrap.sh` creates the OIDC provider and the
deploy role, pins the trust policy to a `SUBJECT` you pass in, attaches a deploy policy scoped to
the services this stack creates (not `PowerUserAccess`; `iam:*` narrowed to `role/taxcalc-lambda-*`),
and prints the role ARN plus the `gh variable set` commands. It runs against a real account to do
the setup, or against floci to author and inspect the policy without one — which is how the
policy above was produced.

`vars.AWS_REGION` is set on the repository. `vars.AWS_DEPLOY_ROLE_ARN` is deliberately **not** set:
a placeholder ARN would turn a clear "variable is missing" error into a confusing assume-role
failure. It gets set when a real account exists.

### Teardown — both stacks

```
sam delete --stack-name taxcalc-lambda-dev      --region $AWS_REGION   -> Deleted successfully
sam delete --stack-name taxcalc-lambda-sandbox  --region $AWS_REGION   -> Deleted successfully

describe-stacks taxcalc-lambda-dev      -> Stack with id taxcalc-lambda-dev does not exist
describe-stacks taxcalc-lambda-sandbox  -> Stack with id taxcalc-lambda-sandbox does not exist

list-tables / list-functions / get-apis / describe-log-groups / describe-alarms -> all empty
```

`taxcalc-lambda-sandbox` is normally created by the CI deploy job, which cannot run without the
role ARN — so it was stood up under that exact name and torn down alongside `dev`, proving the
teardown path works for both. Emulator, with the same caveat as everywhere else on this page.

**Still genuinely open — these need real AWS and nothing else will do:**

| Graded artefact | Why an emulator cannot stand in |
|---|---|
| SnapStart cold-start improvement | The measurement above sizes what SnapStart would remove, but the post-SnapStart cold number needs Firecracker restore on real AWS. |
| `list-metrics --namespace TaxcalcDev` | floci stores the EMF line as an ordinary log line and never parses it (`{"Metrics": []}`). The template-side risk is now closed though: AWS documents that *"Lambda doesn't double-encode any logs that are already JSON encoded"*, so `_aws` stays at the root under `LogFormat: JSON` — the earlier worry about the ALC envelope swallowing it was unfounded. |
| Alarm's `ExtendedStatistic` | floci stores the alarm but drops this field (and `TreatMissingData`), so `describe-alarms` reports `None` where AWS would report `p99`. Traced to floci's **CloudWatch implementation**, not its CloudFormation transform: `put-metric-alarm --extended-statistic p99` called directly against the API round-trips as `None` too, so there is no local path to the value. Everything else on the alarm does survive — see below. |
| CloudWatch `REPORT` lines *in the deployed log group* | floci emits none, so `sam-smoke.sh`'s check is skipped there via `EXPECT_RUNTIME_REPORT=false`. Partly closed though: the RIE **does** emit real `REPORT` lines locally (they are the source of the latency table above), so the format the script greps for is confirmed against the real runtime — only their delivery into CloudWatch Logs is unverified. |
| OIDC CI deploy | GitHub↔AWS STS trust plus a real Actions run; no local equivalent by construction. |

Two further floci quirks worth knowing before repeating this: its CloudFormation Outputs report the real-AWS-shaped `https://{id}.execute-api.{region}.amazonaws.com` hostname, which does not resolve locally — the API is actually served at **`http://localhost:4566/execute-api/{apiId}/{stage}`** (the LocalStack-style `/restapis/.../_user_request_/` and `*.localhost.localstack.cloud` forms all 404; cf. upstream issue [#1902](https://github.com/floci-io/floci/issues/1902)). And `get-template --template-stage Original` returns the *post*-transform template (`TaxpayerLookupFunction` has `Type: AWS::Lambda::Function`), so you cannot inspect what was actually submitted. To keep the smoke script usable in both worlds without a second drifting copy, `scripts/sam-smoke.sh` gained two overrides that both default to real-AWS behaviour: `HTTP_API_URL` (bypass Outputs resolution) and `EXPECT_RUNTIME_REPORT` (skip the REPORT assertion). Never set the latter to `false` for a real AWS run — that check is what catches a function and a log group that have drifted apart.

(Unrelated local-environment note, no repo change: this laptop sits behind Zscaler TLS interception whose root CA is in the macOS System keychain but in no JDK truststore, so Maven Central and Gradle only resolve with `-Djavax.net.ssl.trustStore` pointed at a keychain-derived store. GitHub Actions runners are unaffected.)

What *was* run and did pass, locally:

```bash
sam validate --lint --region us-east-1   # -> "template.yaml is a valid SAM Template"
mvn -B -ntp test                         # -> Tests run: 13, Failures: 0, Errors: 0
mvn -B -ntp package                      # -> target/taxcalc-taxpayer-lookup-1.0.0.jar (the deployment artefact)
sam build && sam build --use-container   # -> Build Succeeded, both exit 0 (SkipBuild stages the jar)
sam local invoke TaxpayerLookupFunction --event events/get-taxpayer.json \
  --env-vars local-env.json --docker-network bridge
# -> {"statusCode": 200, ..., "body": "{\"id\":\"txp_synth_001\",...,\"totalLiability\":14235.50}"}
#    With DYNAMODB_ENDPOINT_OVERRIDE pointed at a seeded local DynamoDB. Without it the same
#    command still exits 0 but returns a shaped 500, since the GetItem reaches real AWS and is
#    rejected for want of credentials.
./gradlew compileJava compileTestJava    # -> BUILD SUCCESSFUL; the Gradle/Maven split holds
```

All three build commands now exit 0 on this machine, `--use-container` included. It previously did not: with a containerised Maven build, dependency resolution died on this laptop's Zscaler TLS interception (`PKIX path building failed ... unable to find valid certification path`), because the build container's truststore has no corporate root CA. `SkipBuild` removed the containerised Maven run entirely — there is nothing left inside the container to download.

```bash
./scripts/sam-deploy.sh    # sam validate --lint -> sam build --use-container -> sam deploy -> print Outputs
./scripts/sam-smoke.sh     # resolve HttpApiUrl from stack Outputs; known-good path + correlation-id echo, route-miss 404, CloudWatch REPORT check
sam delete --stack-name taxcalc-lambda-dev --region "$AWS_REGION"   # teardown is part of the deliverable, not an afterthought
```

## Week 5 Day 5 — Observability: Prometheus, Grafana, Loki, Tempo & OpenTelemetry

W5 D1-D4 shipped the deploy mechanics; today the same k3d Deployment becomes answerable. `manifests/observability/` adds a `ServiceMonitor` (`release: kube-prometheus-stack` on the object itself — the Operator's selector is scoped to that label by default, and a ServiceMonitor without it is simply never selected, with no error and no failing target), a strategic-merge Deployment patch attaching the OpenTelemetry Java agent, a Sloth-generated `PrometheusRule`, an `AlertmanagerConfig` splitting `severity: page` from `severity: ticket`, and `LABELS.md`. `.grafana/dashboards/taxcalc-api-red.json` is the five-panel RED dashboard (rate by status, 5xx *fraction* rather than count, p50/p95/p99 with `exemplar: true`, in-flight, and the custom business counter), shipped as git-tracked JSON and rebuilt into a ConfigMap from that file on every apply rather than hand-pasted into a manifest. `slo/taxcalc-api.sloth.yaml` declares one SLO — 99% of `GET /api/v1/taxpayers/{id}` under 500ms **and** non-5xx over 30d — and `sloth generate` compiles it into the committed rule that CI re-generates and diffs.

On the Spring side: `micrometer-registry-prometheus` + a narrowed `management.endpoints.web.exposure.include: health,prometheus,info` (never `*` — `heapdump`, `env` and `threaddump` would then be reachable unauthenticated), `logback-spring.xml` writing one JSON object per event under the `prod`/`k8s` profiles, `TaxpayerLookupService` owning the application's meters and its `@WithSpan` child span, and `CorrelationIdFilter` threading one caller-visible id through all three pillars.

**Where this repo's shape differs from the generic brief, and why.** The graded endpoint here is `GET /api/v1/taxpayers/{id}` (URI-versioned since W3 D2), not `/taxpayers/{taxpayerId}`, and it is JWT-gated — so every query, the dashboard, the SLI and the smoke script use the real path. There was no W3 D2 correlation-id filter to build on (the brief assumes one); `CorrelationIdFilter` is new here. There is no `TaxpayerLookupService` in the pre-existing code either, and the obvious place to put meters — `TaxLiabilityService.findById` — is exactly the wrong one: it is `@Cacheable` against Redis, so Spring's cache proxy returns a hit **without entering the method body**, and meters inside it would have counted cache misses while claiming, by name, to count lookups. The new service is a seam *outside* that proxy. The brief also names the observability namespace `monitoring` in its prerequisites and `observability` in its appendix; everything here uses `monitoring`, consistently.

**Two panel queries in the brief do not work against this application, and were caught by running them rather than by reading them.** `http_server_active_requests` does not exist on Spring Boot 3.x - in-flight requests are published as a LongTaskTimer, so the count lives in `http_server_requests_active_seconds_gcount`, and a panel querying the older flat gauge renders empty forever with no error. And the Errors panel's 5xx fraction returns an *empty vector* rather than zero when nothing is failing (PromQL drops unmatched elements), so a perfectly healthy service shows "No data" - indistinguishable from a broken panel. `or vector(0)` fixes it, the same defect and the same fix as the SLI below.

**Three deliberate departures from the brief's reference snippets, each one a bug in the shape it suggests:**

1. **`action: labeldrop`, not `action: drop`.** The reference ServiceMonitor drops the high-cardinality `exception` label with `sourceLabels: [__name__, exception]` / `action: drop`, which discards *the whole time series* whenever `exception` is non-empty — silently deleting every 5xx series and pinning the Errors panel at zero, the one panel whose emptiness looks like good news.
2. **The init container copies the agent from OpenTelemetry's own `autoinstrumentation-java` image** instead of `curl`-ing the jar from a GitHub release on every pod start. The reference shape makes every rollout, restart and scale-up depend on github.com being reachable from inside the cluster, and pipes an unverified download straight into `-javaagent`.
3. **The SLI counts fast 5xx responses as budget burn**, not just slow ones. A latency-only SLI scores an endpoint that instantly returns 500 as a perfect month.

**Two conflicts with the existing codebase that had to be resolved, both invisible if you only read the diff.** This app has carried the OpenTelemetry *Spring Boot starter* since W3 D5; attaching the *agent* on top means both instrument Spring MVC, JDBC and Kafka, and every request produces two parallel span trees. The `k8s` profile therefore sets `otel.sdk.disabled: true`, which is read by the starter only — the agent configures itself from system properties and environment variables and never reads `application.yml`, so this disables exactly one of the two (setting the `OTEL_SDK_DISABLED` *env var* instead would disable both and leave the app with no tracing at all). That switch has a second-order effect worth naming: the starter's fallback then publishes `OpenTelemetry.noop()`, and `TaxLiabilityService` **injects that bean** to capture trace context onto each outbox row (W3 D5). Nothing would fail; traces would just quietly stop connecting across the Kafka hop. `AgentOpenTelemetryConfig` rebinds the bean to the agent's global instance for that profile. Separately, W5 D1's `jlink`-trimmed JRE contains only the modules `jdeps` found in application bytecode — and an agent is not application bytecode, so `java.instrument` was missing and the JVM refused to start at all once `-javaagent` was added, before the first application log line.

### Verified live against the k3d cluster from W5 D3

```bash
./scripts/observability-bootstrap.sh   # PLG-T into `monitoring`, once per cluster
./scripts/observability-apply.sh       # sloth drift -> promtool -> unit tests -> dashboard -> apply -> rollout
./scripts/observability-smoke.sh       # one request, three pillars
```

- **Task 1** — every one of the dashboard's five panel queries was run against live Prometheus and returns data (the 5xx-by-uri series is legitimately empty: nothing is failing in this cluster, and the companion fraction target reads `0`). All three `taxcalc-api` pods report `up` as Prometheus targets with no `lastError`; `/actuator/prometheus` serves 228 `http_server_requests_seconds_bucket` series including the `le="0.5"` boundary the SLI reads; the dashboard's own panel queries return real numbers (p99 `0.0266s`, rate by status, `taxcalc_liability_recomputed_total` by outcome). Grafana serves `uid: taxcalc-red-v1` from the `Capstone` folder with all five panels at `schemaVersion: 39`, `meta.provisioned: true`, and the rendered provider inside the pod carrying `allowUiUpdates: false`; all four datasources (Prometheus, Loki, Tempo, Alertmanager) resolve.
- **Task 2** — pod stdout is a JSON envelope carrying `correlationId`, `trace_id`, `span_id`, `app` and `env` as top-level fields; the literal `{app="taxcalc-api"} |= "lookup"` run through Grafana's Loki datasource returns the request's own line **immediately** (budget: 60s), and the shipped stream carries exactly the four labels `LABELS.md` documents — `app`, `env`, `level`, `pod`. Note the query shape: the stream is selected by *label*, the identifier is a *line filter*. Two lines per pod are not JSON and cannot be: `Picked up JAVA_TOOL_OPTIONS` and an arm64 SVE warning are written by the JVM launcher itself, before any logging framework exists, so `kubectl logs | head -1 | jq .` fails on them by construction while all 323 Logback lines parse. The workaround was considered and rejected on purpose: HotSpot has no flag to suppress that notice (`_JAVA_OPTIONS` and `JDK_JAVA_OPTIONS` print their own), and the distroless image has no shell to redirect stderr with, so the only way to remove it is to stop setting the variable and spell the JVM flags out in an explicit `command:` - which buys this check by breaking the next task's requirement that the agent be *attached via `JAVA_TOOL_OPTIONS`*, and copies the image's entrypoint into a manifest where a Dockerfile change would silently desync it. Two graded lines conflict; the attach mechanism wins, because it is the one describing how the system actually works. Everything that *can* be made JSON now is: the agent's own startup logging is routed through Logback (`OTEL_JAVAAGENT_LOGGING=application`) and Spring's seven-line ASCII banner is off in the `k8s` profile.
- **Task 3** — a single trace fetched from Tempo by id shows `POST /graphql` (carrying the `correlation.id` span attribute) → `TaxpayerLookupService.findById` (the `@WithSpan` child) → `find taxcalc_dev.taxpayers` (Mongo) → `SELECT taxcalc.taxpayer` (Postgres). One span tree, not two, which is the double-instrumentation fix holding. TraceQL search by `{ resource.service.name = "taxcalc-api" }` returns traces; Prometheus stores exemplars on 10 `http_server_requests_seconds_bucket` series; and following one exemplar's `trace_id` resolves in Tempo **and** returns that request's log lines from Loki - the trace ⇄ metric ⇄ log triangle closed in both directions.

  **Exemplars needed application code, which is the part that looks finished and is not.** Prometheus ran with `--enable-feature=exemplar-storage`, the dashboard's p50/p95/p99 targets set `exemplar: true`, and the agent produced good spans - and the exposition still carried zero exemplars, so no diamond could ever appear. Spring Boot only wires exemplar support into `PrometheusMeterRegistry` when a `SpanContext` bean is present, and the one it autoconfigures comes from *Micrometer Tracing*, which this app does not use: tracing here is the Java agent, which Micrometer knows nothing about. `OpenTelemetryExemplarConfig` bridges them by reading `Span.current()` directly. Only sampled spans are offered as exemplars - an exemplar pointing at a trace the sampler dropped is a link to a 404. After the fix the same endpoint serves 61 exemplar-carrying lines.
- **Task 4** — `sloth generate` is byte-stable against the committed rule; `promtool check rules` reports `SUCCESS: 17 rules found`; both burn-rate alerts load into Prometheus and sit `inactive`; the SLI recording rules evaluate to real ratios (`ratio_rate5m = 0` on healthy traffic).
- **Round trip** — `observability-smoke.sh` passes all five assertions for one request: `metric`, `business metric`, `log`, `trace`, each keyed to an id the script invented for that run.

**Two of Task 4's Done-when commands could not pass as the brief writes them, and both were resolved rather than excused.** `promtool check rules` rejects a Kubernetes CR outright (`field apiVersion not found in type rulefmt.RuleGroups`) and has no lenient mode - `--lint=none` disables linting, not parsing - while `kubectl apply` needs exactly that CR, so no single file can satisfy both commands. `scripts/slo-render.sh` therefore renders **two** artefacts from the one Sloth spec: the PrometheusRule CR and a flat `slo/taxcalc-api.rules.yaml`. That is not a second source of truth - nobody edits either, both are regenerated and byte-diffed in CI, and `promtool check rules slo/taxcalc-api.rules.yaml` now exits 0 against a committed file with no preprocessing. The same renderer gives the two burn alerts distinct names, `taxcalc-apiLatencySLOBurnFast` and `…Slow`, which Sloth cannot do on its own (one name per SLO, no per-alert override). Doing it in the renderer keeps it a build step rather than the hand-edit the drift gate exists to catch - and it is better naming regardless: the alert name is what appears in a pager notification, a silence and a runbook title, and with one shared name a silence on the slow burn also silences the page.

**`Reconciled: True` does not exist on a PrometheusRule** at any Operator version this chart ships - v0.77.1 exposes exactly one feature gate (`PrometheusAgentDaemonSet`), and status for configuration resources is not it. The condition that does exist, and that actually gates whether any rule is live, is on the **Prometheus CR**, which flips to `Reconciled=True` once the Operator has rebuilt the rule files and reloaded Prometheus. `observability-apply.sh` prints it as its last step:

```
[7/7] operator reconciliation
  Available=True ()
  Reconciled=True ()
```

**The burn-rate alert, fired for real.** With a temporary 700ms delay injected into the request path and `hey -z 240s -c 20` against `GET /api/v1/taxpayers/{id}`, the SLI moved and both alerts fired on schedule:

```
  t+ 0s   ratio5m=0.0   ratio1h=0.0     page=inactive  ticket=inactive
  t+20s   ratio5m=1.0   ratio1h=0.0573  page=inactive  ticket=inactive
  t+40s   ratio5m=1.0   ratio1h=0.2088  page=inactive  ticket=firing
  t+60s   ratio5m=1.0   ratio1h=0.2088  page=firing    ticket=firing
```

That ordering is the multi-window design working, not a race: the 5m window hit 100% within twenty seconds, and the page alert still refused to fire until the 1h window also crossed 14.4% - which is exactly what stops a brief spike from waking anyone. In Alertmanager the firing alert carried `severity=page`, `team=taxcalc`, its runbook annotation, and routed to `taxcalc-dev/taxcalc-api-routing/taxcalc-pager` - the pager receiver, not the default. (That run predates the rename; the same alert is now `taxcalc-apiLatencySLOBurnFast`, confirmed loaded in Prometheus after the Operator's reload.) The fault injection was a throwaway filter and a throwaway image tag; neither is committed, and the deployment was rolled back to the clean image afterwards (verified: the same endpoint back to 34ms).

**Getting that fault to register took a finding worth keeping.** The first attempt injected the delay into `CorrelationIdFilter` and moved nothing at all - the SLI sat flat at `0.0` through five minutes of 750ms responses. `CorrelationIdFilter` is ordered at `HIGHEST_PRECEDENCE` so that it wraps the security chain, which also places it *outside* Spring Boot's `ServerHttpObservationFilter` (`HIGHEST_PRECEDENCE + 1`) - so every millisecond it spends is invisible to `http_server_requests`. Worth knowing beyond this demo: latency added by anything ordered outside that filter (an auth proxy filter, a request-logging wrapper, a decompression filter) does not appear in the very metric the SLO is computed from, and the service looks fast while users wait. The fault had to be re-injected at order `-1000` - inside the observation filter, outside security - before the histogram saw it.

**The alerts are also unit-tested, not only hand-triggered.** `slo/taxcalc-api.rules_test.yaml` feeds synthetic series through the real recording and alerting rules (`promtool test rules`): a healthy service pages nobody, a sustained 50% breach pages, a two-minute blip tickets *without* paging, and fast 5xx responses still burn the budget. That last case failed on first run with **zero alerts**, which is a real defect and the worst possible one — PromQL binary operators drop elements with no match on the other side, so when every request is a 5xx the "good" selector matches no series, `total - good` evaluates to an empty vector, and a total outage looks exactly like a quiet weekend. Fixed with `or vector(0)`. The unit tests and the live burn answer different questions and both are worth having: the live run proves the whole chain (traffic → histogram → recording rule → alert → Alertmanager → receiver), while the unit tests pin the thresholds deterministically, in a second, on any machine - including the total-outage case that a live demo would be unlikely to stage.

**Also fixed in passing:** `./gradlew check` was red on `main` before today — W5 D3's readiness group names `db` and `mongo`, which the `test` profile disables, and Boot validates group membership at context refresh, so all 33 `@SpringBootTest` classes failed to start with `NoSuchHealthContributorException`. The `test` profile now overrides the group.

### Findings from actually standing the stack up

Every one of these was silent or actively misleading, and none is visible in a diff:

- **`management.distribution` binds happily and means nothing.** `distribution:` was first written as a sibling of `metrics:` rather than under it. No warning; the only symptom is that `_bucket` series never appear, which looks exactly like "this endpoint has had no traffic yet". Caught by curling a live pod and finding zero `http_server_requests_seconds_bucket` lines while this service's *own* timer — which asks for a histogram in Java rather than YAML — was emitting 69. The meter-name keys are also bracketed (`"[http.server.requests]"`), since they contain dots.
- **Disabling the Prometheus Operator's admission webhooks crash-loops the operator.** The same certificate serves its liveness endpoint on `:10250`, so the probes hit a port with nothing listening — `exit 137`, repeating, with no hint that a webhook setting caused it.
- **The chart's 1s operator probe timeout is wrong for a laptop cluster.** Under load the TLS handshake intermittently exceeds a second, three in a row kill the container, and the restart makes the next handshake slower still.
- **Grafana's dashboard sidecar with `searchNamespace: ALL`** needs cluster-wide ConfigMap RBAC the chart does not grant here; the watch loop dies at startup (`Process for ALL/configmap died`) and takes the whole Grafana pod down.
- **Loki's `persistence.enabled: false` mounts nothing in the PVC's place**, and Loki dies on `mkdir /var/loki: read-only file system`. The emptyDir has to be supplied explicitly.
- **The apply script's original ordering overwrote the real dashboard with its own placeholder** on every run — invisible until someone opened Grafana.
- **The trace → logs pivot was wired to a datasource UID that did not exist.** Grafana assigns a generated UID (`P8E80F9AEF21F6940`) to any datasource that does not pin one, so Tempo's `tracesToLogsV2.datasourceUid: loki` pointed at nothing and the "Logs for this span" button silently never rendered.
- **Then it rendered and returned nothing**, because Grafana builds that pivot's stream selector from OpenTelemetry's `service_name` convention — a label this pipeline deliberately does not ship, since Alloy sends only the four in `LABELS.md` and Loki's own `service_name` discovery is off for the same reason. The Loki pane read "No logs found", indistinguishable from missing logs. Mapping the span's `service.name` resource attribute onto the `app` label fixes it; `trace_id` stays a line filter, because it is a field in the JSON body and never a stream label. Worth noting the shape: a correct decision in one component quietly invalidated a default in another.
- **`kubectl port-forward` beats an in-cluster curl pod** for the smoke script: no extra image to pull, and it behaves identically on a laptop and a CI runner.

**Local-environment notes, no repo change** (same class as the W5 D4 Zscaler note): this cluster cannot pull from *any* registry — the interception CA is in the macOS keychain but not in the k3d nodes' containerd trust store — so `scripts/observability-preload-images.sh` pulls on the host and `k3d image import`s. That script reads the architecture from the cluster's own nodes after importing amd64 images onto arm64 nodes: they *run*, under emulation, several times heavier, and the smallest sidecars get OOMKilled (`exit 137`) while the big containers stay up, with nothing anywhere saying "wrong architecture". The same CA problem breaks Gradle dependency resolution inside the Docker build stage (`PKIX path building failed`), so the images verified here were built with the host-built `bootJar` substituted for the builder stage via `docker build --build-context builder=…`; the committed `Dockerfile` is unchanged and CI builds it end to end. The stack also needs roughly 1.5GB more than a default Rancher Desktop VM has: below that, containers are SIGKILLed at random and it reads as a dozen unrelated bugs.

**Known gap:** the PR's dashboard, trace-to-logs and burn-rate *screenshots* are not in this README — they need a browser session against the live Grafana, which is a person's job, not this branch's.

### The CI gate

`.github/workflows/observability.yml` runs two jobs. **`static-gates`** needs no cluster and finishes in about a minute: the Sloth drift gate (regenerate, `git diff --exit-code`), `promtool check rules`, the four `promtool test rules` alert cases, dashboard JSON sanity (`schemaVersion == 39`, the pinned `uid`, panel count, at least one exemplar target), and `kubeconform -strict` with the CRD catalog. **`cluster-round-trip`** builds the image, stands up an ephemeral k3d cluster, installs the PLG-T stack and runs the same `observability-apply.sh` + `observability-smoke.sh` a developer runs, uploading diagnostics on failure.

Both generators run from **pinned containers** rather than downloaded binaries, and that came from CI failing: Sloth stamps its own version into the generated rule, and Homebrew's build reports `0.16.0` where the GitHub release binary reports `v0.16.0` — a byte-stability gate whose output depends on how the tool was installed fails for nobody's mistake. `kubeconform` also needed teaching about the new tree (the CRD catalog for the three Operator CRs, and two exclusions: the Helm values files are not Kubernetes objects, and the Deployment patch is a strategic-merge *fragment* with no selector by design).

The cluster job runs with `OBS_VALUES_OVERLAY=ci`, which layers `manifests/observability/helm/ci/` over the base values: a GitHub runner is 2 vCPU / 7GB and also holds k3d, the app, Postgres, Mongo and Redis, and the full stack does not fit — the first attempt left half the monitoring namespace `Pending`. The overlay drops Grafana and Alertmanager and shrinks every request. Both are dropped because *nothing in the round trip queries them* — the smoke talks to the Prometheus/Loki/Tempo APIs directly, the dashboard JSON is validated in `static-gates`, and the alerts are unit-tested there — not to make a failing assertion pass. The job also scales the Deployment to one replica and removes the HPA first, since W5 D3's 3 replicas at 250m with a floor of 2 are the difference between the monitoring pods scheduling and never scheduling.

```bash
./scripts/observability-preload-images.sh   # only on a TLS-intercepted network
./scripts/observability-bootstrap.sh        # kube-prometheus-stack + Loki + Tempo + Alloy + OTel Collector
./scripts/observability-apply.sh            # the app's own observability layer, drift-gated
./scripts/observability-smoke.sh            # metric + business metric + log + trace for one request

OBS_VALUES_OVERLAY=ci ./scripts/observability-bootstrap.sh   # the trimmed stack CI installs
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/w" -w /w ghcr.io/slok/sloth:v0.16.0 \
  generate -i slo/taxcalc-api.sloth.yaml -o manifests/observability/taxcalc-api-prometheusrule.yaml
```

## Week 6 Day 1 — GitHub Actions CI/CD & OIDC Federation to AWS

W5 D1 shipped a hardened Docker image (`uptimecrew/taxcalc-api:0.1.0`, hadolint + Trivy + a 60-second smoke test) built entirely by hand on a laptop — every `docker build`, every scan, every `docker push` was still a person running commands. Today that ends: `.github/workflows/ci.yml` adds a `build-test` job (`./gradlew build` — compile, the full JUnit 5 suite across ~35 Testcontainers-backed `@SpringBootTest`/`@DataJpaTest`/`@DataMongoTest` classes, and the JaCoCo 70% branch-coverage gate) that is now a required PR status check — the first CI job in this repo that actually runs the test suite rather than skipping it (`docker.yml`'s own build stage runs `bootJar -x test`, deliberately). On merge to `main`, a new `call-build-and-push` job invokes the reusable `.github/workflows/_build-and-push.yml`, which assumes an OIDC role (`taxcalc-api-build-push`, no long-lived AWS keys anywhere in this repo), builds the image, Trivy-scans it, and pushes it to ECR tagged both by git SHA (immutable) and `main` (a dev-convenience pointer — never `latest`, per `docker/SECURITY.md`'s existing rule). `.github/workflows/deploy-prod.yml` promotes a SHA-tagged image to prod manually (`workflow_dispatch`), gated behind the `prod` GitHub Environment's required reviewers and a narrower `taxcalc-api-prod-deploy` OIDC role.

**No `taxcalc-api/` subdirectory, again.** Every path this deliverable's own reference spec names under `taxcalc-api/.github/...`/`taxcalc-api/infra/...` lives at this repo's root instead — `.github/PIPELINE.md`, `infra/oidc/`, same reasoning `docker/SECURITY.md` already documents for `Dockerfile`/`.hadolint.yaml`. **`ubuntu-24.04` for `build-test`, Blacksmith everywhere else — and that split is now a choice rather than a constraint.** The spec's generic runner would not even queue in this org after the GitHub Actions billing block W5 D2 worked around, so every workflow here moved to `blacksmith-2vcpu-ubuntu-2204`. Public repositories get free standard GitHub-hosted runners, so making this repo public on 2026-09-04 lifted that block — **measured before acting on it**, since the block was org-level and might not have followed repo visibility: a throwaway `ubuntu-24.04` job booted on a Hosted Compute Agent and passed (run `33851940650`). `ci.yml`'s `build-test` therefore now uses the spec's literal runner, closing a deviation that had been recorded as forced. The rest deliberately stay on Blacksmith on measured performance grounds — `observability.yml`'s cluster job takes 5m23s there against 13m52s on a GitHub-hosted runner — which is a trade-off, not a workaround. **JDK 17, not 21**, in the new composite action — `build.gradle`'s toolchain pin, same constraint the Dockerfile's `builder` stage already documents.

**A real bug found by running the first CI attempt, not by reading the YAML.** The composite action at `.github/actions/setup-build/action.yml` was first written with `actions/checkout` as its own first step — mirroring how the lesson describes the "checkout + setup-java + gradle cache" bundle. That failed immediately: `Can't find 'action.yml' ... Did you forget to run actions/checkout before running your local action?`. A local `uses: ./local-action` reference can only be resolved after the repository is already on disk, since the runner has to read the action's own definition file to know what it does — so a composite action cannot supply the very checkout that makes it resolvable in the first place. Fixed by dropping checkout from the composite action (JDK setup + Gradle cache only) and giving every caller (`ci.yml`'s `build-test`, `_build-and-push.yml`'s `build-scan-push`) its own explicit `actions/checkout` step immediately before calling it — confirmed green on the next push.

**`infra/oidc/`** holds the committed, reproducible shape of both IAM trust policies (`trust-policy-build.json`, `trust-policy-prod.json`) and `scripts/docker-oidc-bootstrap.sh` (sibling to the existing `scripts/oidc-bootstrap.sh`, same structure) applies them against a real AWS account. The build role's trust policy pins `sub` to *both* `environment:dev` and `ref:refs/heads/main` (belt-and-suspenders); the prod role pins to `environment:prod` **only** — deliberately no branch condition, since `workflow_dispatch` can run from any branch a caller can push to, and the Environment's required-reviewer gate (not a `sub` string) is what actually decides whether the run starts.

**The subject claim was measured rather than assumed, and the measurement caught a bug that would have broken every deploy.** Both policies were first written with the textbook `repo:<org>/<repo>:...` form. `.github/workflows/oidc-probe.yml` — a diagnostic workflow that mints a real OIDC token, decodes it, and diffs the observed subject against what `infra/oidc/*.json` actually pins — reported a **mismatch**: this organization issues a subject carrying internal numeric org/repo ids, exactly as `oidc-bootstrap.sh`'s W5 D4 header comment warned it might.

```
"sub": "repo:AI-Native-2026-07-29-Intuit@309728071/arush-adabala-tax-liability@1317703842:pull_request"
"aud": "sts.amazonaws.com"
```

Both trust policies and the bootstrap script's `SUBJECT_*` defaults now carry that real prefix. Left unmeasured, the first deploy would have failed with `Not authorized to perform sts:AssumeRoleWithWebIdentity` — an error naming neither `sub` nor the value AWS expected, against a policy that reads perfectly correctly. The *prefix* is now observed; the three *suffixes* remain inferred from GitHub's documented rules, since a `pull_request` run cannot mint an environment-scoped token and both Environments are pinned to `main` — re-running the probe by `workflow_dispatch` after the first push to `main` confirms them.

**The trust-policy decision is simulated rather than mocked, because mocking it would prove nothing.** Standing up an emulator holding a role and a trust policy does not answer "would AWS allow this?" — emulators return credentials for any `AssumeRoleWithWebIdentity` without evaluating the statement's conditions at all, so such a mock passes just as happily against a wrong `sub`, which is exactly the bug that was really present. `scripts/oidc-trust-simulate.py` reproduces the decision procedure instead, which is small and fully documented: confirm `Principal.Federated` names the GitHub provider, then evaluate `aud` and `sub` under IAM `StringEquals`/`StringLike` semantics. Both inputs are already in hand, so the outcome is computable offline and deterministically, with no emulator approximating anything. `oidc-probe.yml` runs it on the prefix that run actually measured — not a hardcoded one — so the committed policies are always checked against today's truth.

It was validated against known-bad inputs rather than trusted because it printed PASS: the pre-fix textbook-form policy is **denied**, naming the exact `sub` mismatch (it would have caught the deploy-breaking bug before a deploy existed to break); a policy with **no** `sub` condition — which would trust every repository on GitHub — is denied, since absence is treated as a failure rather than a pass; and a `repo:ORG@id/*` wildcard is shown allowing a *different* repo in the same org. Its own run carries three negative controls, so a simulator that only ever answers ALLOW fails its own suite.

**The token's own validity is fully verified, by running AWS's check rather than approximating it.** AWS STS holds no private knowledge when validating a GitHub OIDC token: it fetches GitHub's *public* JWKS from the issuer's discovery document and verifies the RS256 signature. `oidc-probe.yml` therefore performs that identical check — and the control is what makes it worth anything: the same token with its signature destroyed must be rejected, and the step exits non-zero if the verifier accepts it.

```
genuine token: SIGNATURE VALID against GitHub's published JWKS
tampered token: correctly REJECTED (InvalidSignatureError)
```

So everything true *about the token* is confirmed — correctly signed by GitHub, `iss` as expected, `aud: sts.amazonaws.com`, unexpired, and carrying a `sub` the trust policies now actually pin. What remains unverified is no longer a property of the token at all: it is account-side configuration (the OIDC provider registered with the right thumbprint and client id, the two roles existing, the trust policies attached). No token experiment can settle that — which is a category difference, not a harder version of the same question.

**The account-side resources were then actually created, against the floci emulator this repo already uses for exactly this gap (W5 D4).** `scripts/docker-oidc-bootstrap.sh` runs unmodified against `AWS_ENDPOINT_URL=http://localhost:4566` and creates all five: the OIDC provider, both roles, both inline policies, and the ECR repository. `scripts/docker-oidc-verify.sh` then asserts each one — **13 checks, 0 failures** — including the deliverable's literal Done-When, that `aws iam get-role --role-name taxcalc-api-build-push` shows a `sub` condition pinned to this repo plus `dev` plus `main`. It also checks two things the Done-When does not: that the `sub` list has *exactly* two entries (a policy that pins the right two subjects but permits a third is not pinned), and that the applied trust policy is byte-identical to the committed `infra/oidc/trust-policy-build.json` modulo the account-id placeholder — otherwise `infra/oidc/` is decoration rather than the reproducible artefact it claims to be.

The verifier was validated against a known-bad input rather than trusted because it printed green: re-applying the *textbook* `repo:uptimecrew/taxcalc-api:…` form the spec suggests, with a third `repo:ORG/*` wildcard entry added, produces four distinct `FAIL` lines — both missing subjects, the entry count, and the drift from the committed file. A verifier that cannot fail proves nothing.

**Two Task 3 checks genuinely survive this, and the emulator's limits were measured rather than assumed.** floci backs ECR with a plain `registry:2` sidecar, so `docker push` bypasses the ECR control plane entirely. Pushing *different* content to an already-existing SHA tag **succeeded** against floci, where real ECR rejects it with `ImageTagAlreadyExistsException` — so the `IMMUTABLE_WITH_EXCLUSION` reasoning (SHA tags frozen, `:main` alone allowed to move, the thing that otherwise breaks the *second* main build and not the first) is verified as *configuration* but not as *enforcement*. For the same reason `aws ecr describe-images` returns `[]` no matter how many images are pushed, so the "`describe-images` lists the SHA tag" Done-When cannot be satisfied here either. Both need a real account; neither is faked, and `docker-oidc-verify.sh` prints them as open on every emulator run instead of quietly counting them as passes.

**Pointing the pipeline itself at floci was considered, and measuring it is what ruled it out.** If the emulator can create the roles and the ECR repo, the obvious next move is to run CI against it and let the three remaining checks go green. So floci was handed a deliberately forged web-identity token — unsigned, `iss` of `https://evil.example.com`, and a `sub` of `repo:someone-else/their-repo:ref:refs/heads/main` — and asked to assume the build role. It returned working credentials:

```
"Arn": "arn:aws:sts::000000000000:assumed-role/taxcalc-api-build-push/floci-theatre-test"
"SubjectFromWebIdentityToken": "web-identity-subject"   # placeholder - never parsed the token
"Provider": "accounts.google.com"                       # not even GitHub
```

That ARN is *precisely* the string Task 3's Done-When tells you to find in the run log. An emulator-backed pipeline would print a convincing `Authenticated as arn:aws:sts::…:assumed-role/taxcalc-api-build-push/…` with the trust policy **deleted**, or pinned to a stranger's repository — it would manufacture the evidence rather than produce it, and the check that looks most satisfied would be the one carrying no information at all. Combined with the two limits already measured (immutability unenforced, `describe-images` blind to pushed images), floci closes **zero** of the three remaining checks while making a run look closed. So the AWS path stays inert until a real account exists, which is what `_build-and-push.yml`'s gating already does.

One further trap worth recording, because it looks like the obvious next step: **do not `gh variable set AWS_ACCOUNT_ID` to the emulator's `000000000000`.** That variable is what gates `_build-and-push.yml`'s AWS steps, so setting it would un-gate them in CI — and a GitHub runner cannot reach a floci on a laptop, so `main` would start failing at the assume-role step. The emulator is a local verification target, not a CI backend.

**The same workflow's third probe returned a null result, and is reported as one.** `sts:AssumeRoleWithWebIdentity` needs no AWS credentials, so a repo with no AWS account can still ask the real endpoint whether it accepts a real GitHub token; the call must fail (no role exists), making the *error code* the signal, with a tampered-signature control run to tell "AWS verified the signature" apart from "AWS never looked". Real AWS returned `InvalidIdentityToken` for **both** the genuine and the deliberately-corrupted token — so the probe demonstrates nothing about signature verification, most likely because STS rejects on the unresolvable account/provider before ever inspecting the JWT. The workflow prints that verdict explicitly rather than reporting a green run as a pass. **"Will GitHub's real token be accepted by AWS?" therefore remains genuinely open, and needs an AWS account** — it is the one question in this deliverable with no offline substitute.

### Five places this deliverable departs from the letter of its spec

Each was re-examined rather than left as a footnote; three are now closed outright, one is parameterised, and one is irreducible.

**The negative grep is now a literal zero.** `grep -RIn 'AWS_ACCESS_KEY_ID\|AWS_SECRET_ACCESS_KEY' .github/ infra/ src/` returns nothing. The task body permits these names inside prose comments explaining why the keys aren't used, but the Done-When line says zero, and a check whose result a reader has to adjudicate is a worse check — so the three comments (`_build-and-push.yml`, `serverless.yml`, `infra/oidc/README.md`) now say "long-lived AWS access-key/secret-key pair" and keep the full rationale. The two remaining hits repo-wide are in `scripts/`, outside the Done-When's scope, and are runnable `AWS_ACCESS_KEY_ID=test` command lines for the emulator — rewording those would break copy-paste for no gain.

**`permissions:` is gone from the reusable workflow.** The spec calls this out negatively ("NOT in the reusable workflow itself — the caller's permissions are what get applied") and it was the one thing named that way. `_build-and-push.yml` now carries no `permissions:` block at all and inherits from `ci.yml`'s `call-build-and-push`, which holds the sole grant (`contents: read`, `packages: write`, `id-token: write`). Both files cross-reference each other so the next person adding a registry widens the right one. This is not merely cosmetic: a block in the called workflow reads as though authority is granted there, when deleting the *caller's* `id-token: write` breaks OIDC regardless of what the called file says.

**The Trivy waiver is now visible on every run instead of silently absorbed.** "0 unfixed HIGH/CRITICAL" was true but was resting on 22 waived CVE ids, and a waiver nobody sees is how a 30-day exception quietly becomes permanent. A second, non-gating scan (`exit-code: 0`, `if: always()`) re-runs with the waiver off and publishes the delta to the step summary. **The obvious implementation of that is wrong, which is why it was tested rather than assumed:** Trivy auto-reads `./.trivyignore` whenever `--ignorefile` is absent, so passing an empty `trivyignores` input leaves the waiver *active* and the step would report the waived number while labelling it un-waived. Measured against `debian:12` — 56 findings with no ignore file, 51 with `./.trivyignore` auto-picked up, 56 again with `--ignorefile` pointed at an explicitly empty file. Only the last form actually overrides it, and that is what the workflow does.

Two counting facts surfaced while wiring that up, and both are now computed rather than hardcoded: `.trivyignore` lists **22 CVE ids** while `SECURITY.md` says **23 findings**, because one CVE across two packages is one line but two findings — and the 2026-09-02 libexpat1 addendum made it 24, leaving the old hardcoded "23" in the workflow comment stale. The summary table labels its units explicitly and says why the columns will not match. Separately, the base-image half of the waiver was re-verified today rather than trusted: `gcr.io/distroless/java-base-debian12:nonroot` still resolves to the exact pinned index digest `sha256:a9930cad…`, so Google genuinely has not published a rebuilt image and the CVE-2026-56408 waiver still holds for the stated reason.

**The ECR mutability deviation is real, irreducible, and now cited rather than asserted.** The spec asks for both `--image-tag-mutability IMMUTABLE` *and* a push of both a SHA tag and a floating `main` tag on every build; those contradict, and AWS's own documentation is what settles it: *"After tag immutability is turned on, the `ImageTagAlreadyExistsException` error is returned if you push an image with a tag that is already in the repository. Tag immutability affects **all** tags. You cannot make some tags immutable while others aren't."* Under plain `IMMUTABLE` the first main build is green and the **second** fails on `main` — a failure delayed by one build, the worst way to find it. `IMMUTABLE_WITH_EXCLUSION` is AWS's own answer to exactly this shape, and the documentation's canonical example filter is a floating tag. The spec's intent (reproducible, pinned images) is kept whole; only the letter bends. `ECR_TAG_MUTABILITY=IMMUTABLE ./scripts/docker-oidc-bootstrap.sh` reproduces the literal spec for anyone who wants it, and prints a warning saying what it will break.

**The subject-claim divergence cannot be closed, and closing it would be the bug.** The spec's `repo:uptimecrew/taxcalc-api:…` is not what this organisation issues; pinning it would guarantee `Not authorized to perform sts:AssumeRoleWithWebIdentity` on every deploy. What *was* closeable is the half that was still inferred. `oidc-probe.yml` gained an `observe-environment-subject` job that runs under `environment: dev` on `main` — precisely the context that mints `…:environment:dev` — and **fails the run** if the observed `sub` differs from what `trust-policy-build.json` pins. So the last inferred claim is confirmed automatically on the first push to `main` rather than sitting as a manual TODO in a README. It could not be measured before merge: both Environments restrict deployments to `main`, and loosening a deployment gate to take a measurement is not a trade worth making.

**Every action in `.github/` is pinned** to a full commit SHA with a version comment — all eleven workflows plus the composite action, not just the files this deliverable added. `grep -RIn '@v[0-9]\+$' .github/` returns **zero, down from 32**. A stricter check is also zero (every non-local `uses:` must be 40 hex characters plus a `# v<n>` comment), which matters because it catches the two the task's own anchored regex cannot see at all: `hadolint/hadolint-action@v3.1.0` and `aquasecurity/trivy-action@v0.36.0` both end in a digit and slip straight past a `@v[0-9]\+$` anchor. An earlier revision scoped the pin to this deliverable's own three workflows and wrote the other six off as a deliberate gap; that is not what the Done-When asks for, and the gap is now closed.

Everything is unified on **one SHA per action repo-wide** — `actions/checkout` at two different versions in one repo is exactly the ambiguity the version comment exists to remove, and it would have made Dependabot open two PRs for one bump. Two traps worth recording, because the obvious resolver walks into both: `matching-refs` sorts refs as *strings*, so `v4.9.0` sorts after `v4.10.0` and taking `.[-1]` silently returns the **older** tag (sort with `sort -V`); and an **annotated** tag's `.object.sha` is the tag object, not the commit, so a workflow pinned to it fails at runtime (dereference through `/git/tags/{sha}`). One further wrinkle found only because Dependabot flagged it: a single commit can carry tags across *different major lines* — `pnpm/action-setup@fc06bc12` is simultaneously `v4.4.0`, `v5.0.0` and the moving `v5`, while the moving `v4` tag points at a different commit entirely, so "pin to what `@v4` resolves to" and "pin to the highest `v4.x`" are not the same instruction.

`.github/dependabot.yml` groups weekly SHA-bump PRs by `actions/*`/`aws-actions/*`/`docker/*`, and that is now observed rather than predicted: the first run produced **13 bumps as 3 grouped PRs plus 2 correctly-ungrouped actions** that fall outside the three patterns — #38 `official-actions` ×6, #39 `aws-actions` ×3, #40 `docker-actions` ×4, #41 `pnpm/action-setup`, #42 `hadolint/hadolint-action` — instead of fifteen separate PRs. The `github-actions-author` Claude Skill this deliverable's lesson names for the scaffold-then-audit step was not available in this session's tool listing; the workflow YAML here was hand-authored directly against the cohort checklist instead, with the checklist's own named "common quirks" (`@v4` with no SHA, a redundant `actions/cache` step alongside `setup-java`'s built-in `cache: gradle`) checked for explicitly — see `.github/PIPELINE.md`'s "AI-tool review note".

**Verified live**, not just read: `actionlint .github/workflows/*.yml` (run via the pinned `rhysd/actionlint` container, matching this repo's existing preference for pinned containers over ad-hoc downloaded binaries — see W5 D5's Sloth section) now exits `0` with **no findings at all, unfiltered, across every workflow in the repo**. Getting there took two fixes rather than an excuse. Seven `runner-label` findings (one per Blacksmith-runner job, tree-wide) were being mentally discounted as "expected noise" — which is exactly how a real finding eventually gets skimmed past, and it made this deliverable's own Done-When check unsatisfiable by construction; `.github/actionlint.yaml` declares the two Blacksmith labels, which is actionlint's own documented fix for a self-hosted-class label and what its error message tells you to do. The eighth was a genuine pre-existing `shellcheck` SC2034 in `observability.yml` (W5 D5): its image-import retry loop is a copy of `k8s-ci.yml`'s that dropped the `echo "Import attempt ${attempt}..."` line, leaving the loop variable unused — restored, which both silences the warning and puts the two sibling loops back in parity, rather than renaming the variable to `_` and losing the log line. `ruby -ryaml` round-trip-parses every new YAML file. Both trust-policy JSONs parse cleanly with Python's `json.load`. `shellcheck` is clean on `docker-oidc-bootstrap.sh`. The PR's own `Build + test (taxcalc-api)` check went green end to end against the real Testcontainers suite (~5 minutes), and `call-build-and-push` correctly reported `skipping` on the PR event (it only fires on push to `main`).

**Also done, not just written:** the `dev` and `prod` GitHub Environments now exist for real (created via the GitHub API), each with its deployment-branch policy restricted to `main` only.

**Two pieces of this deliverable were blocked on billing rather than on work, and making the repo public closed both.** `prod`'s required-reviewer rule and branch protection on `main` are *both* plan-gated for private repositories. While this repo was private on a `plan: free` org, `PUT .../environments/prod` returned `422: "Please ensure the billing plan supports the required reviewers protection rule"` (identically for `wait_timer`), and both `GET .../branches/main/protection` and the newer `GET .../rulesets` returned `403: "Upgrade to GitHub Pro or make this repository public to enable this feature."` No permission grant fixed either — the features did not exist on that plan, for anyone, by API or UI. That is the second plan-level GitHub constraint this curriculum has hit here; the W5 D2 Actions billing block that forced every workflow onto Blacksmith runners is the first, and is why `ci.yml` cannot use the spec's `ubuntu-24.04` either.

The repo was made public on 2026-09-04, after a credential audit rather than on faith: all commits scanned for `sk-ant-`, `AKIA`/`ASIA`, `ghp_`/`github_pat_`, Slack tokens and PEM private keys — **zero hits**; the tracked `.env` holds only `APP_VERSION`/`COMPOSE_FILE` Compose interpolation; `secrets/` contains nothing but `.gitkeep`; `manifests/40-taxcalc-api.secret.yaml` carries `replace-at-apply-time-from-secrets-manager`; every AWS account id under `infra/` and `scripts/` is a placeholder. Both rules were then created: `main` requires the `Build + test (taxcalc-api)` status check (**selected by display name** — `ci.yml`'s `name:` override means a rule naming the job id `build-test` would match nothing and silently protect nothing), and `prod` carries required reviewers `ArushAdabala` + `yusufumautiauptimecrew` alongside its existing `main`-only deployment-branch policy. `.github/PIPELINE.md`'s "Repository protection" section records the exact settings, including the two deliberately loose ones (`enforce_admins: false`, `prevent_self_review: false`) and what tightening them would cost.

## Week 6 Day 2 — Argo CD & GitOps: Cutting Over From Push to Pull

W6 D1 shipped a pipeline that reached *into* the cluster: a green build meant "CI held cluster credentials and used them". Today the arrow flips. Argo CD v2.11.7 runs *inside* the k3d cluster, watches a sibling config repo, and pulls. The application repo's pipeline no longer deploys anything — its last step is *"open a PR against the config repo"*, and merging that PR is what deploys. The cluster boundary now accepts only pulls; drift becomes a controller alarm rather than a human discovery; rollback becomes `git revert`.

**Two repos, one boundary.** [`arush-adabala-tax-liability-config`](https://github.com/AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability-config) was created today and holds the desired state: `k8s/taxcalc-api/` (the W5 D3 manifests, verbatim), `overlays/{dev,staging,prod}/`, `argocd/projects/taxcalc.yaml`, `argocd/applications/taxcalc-api-dev.yaml`, `argocd/applicationsets/taxcalc-api-envs.yaml`, `argocd-system/notifications-cm.yaml`, and `platform/` for the things Argo CD is deliberately *not* allowed to manage. This repo gains `.github/workflows/_bump-config.yml` and [`GITOPS.md`](GITOPS.md), which carries the full write-up; this section is the summary and the deviations.

All three Applications reach `Synced` + `Healthy` — `taxcalc-api-dev` 1/1, `taxcalc-api-staging` 2/2, `taxcalc-api-prod` 3/3, each off its own overlay in its own namespace, generated by one `ApplicationSet` matrix (`list(env) × clusters(tier=workload)`).

**Those replica counts took a fix, and the bug they exposed is one this repo already knew about.** Every overlay set its own `replicas`, but `k8s/taxcalc-api/hpa.yaml` sets `minReplicas: 2` for *all* environments, so the HPA pulled dev back up to 2 within a reconcile — and because `Deployment.spec.replicas` is in `ignoreDifferences`, **Argo CD reported `Synced` the entire time**. Git said 1, the cluster ran 2, and nothing flagged it. This is the W5 D3 `replicas`-vs-`minReplicas` conflict (recorded in that section's findings) surviving into GitOps and getting *quieter*, because reconciliation now papers over it. Each overlay patches the HPA floor to match the replica count it asks for. `ignoreDifferences` stays and stays correct — it is what lets the HPA scale **up** under load without Argo CD calling that drift — but the two no longer contradict each other at rest.

### Six things measured rather than assumed — each one changed the YAML

**1. A placeholder Secret in a continuously-reconciled manifest set is worse than no Secret at all.** The W5 D3 `40-taxcalc-api.secret.yaml` carries `replace-at-apply-time-from-secrets-manager`, and under `kubectl apply -f manifests/` that placeholder was **inert**: CI reseeded the Secret from a real store *after* applying, so the last writer held a real password. Continuous reconciliation deletes that ordering entirely. On the very first sync Argo CD faithfully wrote the placeholder over the seeded value and every api pod began failing `FATAL: password authentication failed for user "taxcalc_dev"`. And with `selfHeal: true` a hand re-seed survives exactly one reconcile interval, so the failure comes *back* a few minutes later — materially harder to debug than failing immediately. The file moved to the config repo's `platform/secret/`, applied out-of-band; W6 D3's External Secrets Operator deletes it rather than moving it back.

**2. Re-seeding that Secret with `kubectl apply` is not enough, and the second failure was sharper than the first.** `apply` **merges**, so the `app.kubernetes.io/instance` tracking label written by the earlier sync survived. On the next reconcile the controller saw a resource it still believed it owned that was no longer in Git, and **pruned** it — pods went straight to `CreateContainerConfigError: secret "taxcalc-api-secrets" not found`. It has to be `delete` then `create`, so the object is genuinely untracked.

**3. `preserveResourcesOnDeletion: true` did nothing, because the ApplicationSet template also carried the finalizer.** That setting is not a flag the controller checks at deletion time — it works by **omitting** `resources-finalizer.argocd.argoproj.io` from the generated Applications, so an explicit `finalizers:` block in the template silently overrides it. Removing the `staging` element from the list generator deleted the Application **and every resource behind it**: `kubectl -n taxcalc-staging get deploy,pods` returned `No resources found` within 25 seconds, while the setting said they would be kept. With the finalizer dropped, the same edit leaves the workload running (2/2 ready) and merely stops managing it — and restoring the element **re-adopts the same pods with no restart** (pod ages unchanged across the round trip). The standalone `taxcalc-api-dev.yaml` keeps its finalizer, because deleting *that* is a decommission rather than a refactor. Treating "always add the finalizer" as a blanket rule — which is what the cohort checklist's quirk list nudges toward — would have reintroduced exactly this bug.

**4. Argo CD's `in-cluster` default is not backed by a Secret at all**, so a `clusters` generator label selector has nothing to match. A selector that matches nothing generates **zero** Applications and *reports success* — Healthy ApplicationSet, green dashboard, nothing deployed. `argocd cluster list` shows `in-cluster`; `kubectl -n argocd get secret -l argocd.argoproj.io/secret-type=cluster` returned nothing. The cluster is now registered declaratively as a labelled Secret carrying `uptimecrew.example.internal/tier=workload`.

**5. The textbook `on-sync-failed` trigger throws on any Application that has never synced.** Written as `app.status.operationState.phase in ['Error','Failed']` it logs `failed to execute when condition: cannot fetch phase from <nil>` and the trigger silently does not evaluate — observed on `taxcalc-api-staging` seconds after the first controller restart. The window in which it silently does not evaluate is precisely the window in which a brand-new environment is most likely to fail its first sync. Both triggers now carry a `!= nil` guard, after which `grep -c 'cannot fetch phase'` over the controller log is `0`.

**6. Applying the ApplicationSet over the existing standalone `taxcalc-api-dev` adopted it rather than racing it.** The controller set an `ownerReference` on the existing object instead of creating a second one, so the spec's "delete the standalone Application" step was already satisfied — verified with `kubectl -n argocd get app taxcalc-api-dev -o jsonpath='{.metadata.ownerReferences}'`.

### The drift experiment the spec suggests proves nothing here, and finding out why is the point

`kubectl -n taxcalc-prod scale deployment taxcalc-api --replicas=5` was **not** reverted, and that is correct. `/spec/replicas` is in `ignoreDifferences` on every Application — Git owns the value the Deployment is *created* with, the HorizontalPodAutoscaler owns it from then on. Without the ignore, `selfHeal` and the HPA overwrite each other forever, the Application never settles on `Synced`, and the notifications wired up in Task 4 alert on a fight that is working as designed. It is the one field where "Git is the source of truth" is simply false.

**And an ignored field is a field nothing checks, which is what the `1/1` miss above really was.** `Synced` was reported the whole time dev was running 2 replicas against a Git that said 1. Anything under `ignoreDifferences` needs its own assertion that the live value is what you intended; the sync status will not give you one.

So the experiment has to target a field Argo CD actually owns. Patching `LOGGING_LEVEL_ROOT` from `DEBUG` to `TRACE` in `taxcalc-dev` was reverted in **10 seconds**:

```
time=19:14:59Z msg="Updated sync status: Synced -> OutOfSync" application=taxcalc-api-dev reason=ResourceUpdated
time=19:14:59Z msg=Syncing application=argocd/taxcalc-api-dev syncId=00018-jVkwK
time=19:14:59Z msg="Tasks (dry-run)" tasks="[Sync/-1 resource /ConfigMap:taxcalc-dev/taxcalc-api-config obj->obj (,,)]"
time=19:15:00Z msg="Adding resource result, status: 'Synced', phase: 'Running', message: 'configmap/taxcalc-api-config serverside-applied'"
time=19:15:00Z msg="Updating operation state. phase: Running -> Succeeded, message: ... -> 'successfully synced (all tasks run)'"
time=19:15:00Z msg="Updated sync status: OutOfSync -> Synced" application=taxcalc-api-dev reason=ResourceUpdated
```

**The same patch against `taxcalc-prod` was still un-reverted after 240 seconds, and that is a consequence of `syncWindows` worth stating plainly: a change freeze freezes self-healing too.** The controller logs `Sync prevented by sync window`; prod sat `OutOfSync` with the drift in place. This is not a bug, but it is a trade-off nobody mentions when adding a weekend deny window: for its duration, prod is *unprotected against drift* as well as against deploys, and only a human `manualSync` closes the gap.

### The one claim in this deliverable that was simply false, and how it survived

**`CreateNamespace=true` is denied by `clusterResourceWhitelist: []`.** It was stated as fact in four files. Argo CD implements the option by *injecting* a Namespace into the sync task list, and the injected resource is checked against the project's `clusterResourceWhitelist` like any other. Measured with a scratch AppProject carrying the identical `[]` deny, pointed at a namespace that did not exist:

```
Namespace  taxcalc-nsproof  SyncFailed  resource :Namespace is not permitted in project taxcalc-nsproof
Phase:     Failed
$ kubectl get ns taxcalc-nsproof
Error from server (NotFound): namespaces "taxcalc-nsproof" not found
```

Not "created but unmanaged" — **not created at all**, and the sync fails rather than degrading quietly.

**How it survived is the more useful half.** `platform/00-namespaces.yaml` pre-created all three namespaces before any Application ever synced, so the code path was never exercised. Every Application reported `Synced` + `Healthy` the whole time, and a false claim about *why* sat in four files looking verified. Nothing in a green dashboard distinguishes "this option works" from "this option was never reached."

The design never depended on it, so nothing was broken — but the operational constraint is real and was undocumented: **a new environment must be added to `platform/00-namespaces.yaml` before it is added to the ApplicationSet's element list**, or its first sync fails. `scripts/verify-appproject-guardrails.sh` asserts the `Namespace` deny as one of its five deny paths, so anyone who later widens the whitelist to make `CreateNamespace` work will see that check flip and have to make the call deliberately.

### The notification path, verified against a real induced failure

**Delivery to Slack is verified end to end**, and getting there needed two things the deliverable does not mention.

**A bot token, not the webhook URL the spec asks for.** `service.slack` is the Slack *Web API* integration — it sends `slack-token` as a bearer credential to `chat.postMessage`. A webhook URL placed there is never requested as a URL. Since the rubric asks for both `service.slack` *and* a screenshotted alert, only a bot token satisfies both.

**And the Zscaler root CA.** The controller's egress to `slack.com` is TLS-intercepted and its image doesn't carry the proxy's root, so every send failed with `x509: certificate signed by unknown authority` *before the token was evaluated*. `argocd-tls-certs-cm` doesn't cover it — that's hostname-keyed and Git-only. Fixed with a ConfigMap holding the image's own 137 system roots **plus** the Zscaler chain, mounted with `SSL_CERT_FILE` pointing at it. Mounting the Zscaler root alone would authenticate Slack and break every other TLS target; the bundle has to be additive.

```
00:10:02Z  Sending notification about condition 'on-sync-failed…' to '{slack taxcalc-deploys}'
annotation: notified.notifications.argoproj.io = {"on-sync-failed:…:slack:taxcalc-deploys": 1788567002}
Failed to notify (since the CA mount): 0
```

The `notified` annotation is the receipt — written **only after a successful send**, and what deduplicates the alert so a failing app doesn't re-notify every reconcile. Before the fix it never appeared and `Failed to notify` logged every pass.

**That run fired `on-sync-failed`, which the earlier experiment never did.** A bad image tag degrades *health* while the sync succeeds, so it only ever exercised `on-health-degraded`. Committing a resource the AppProject denies fails the sync operation itself — and lands far sooner, since a health degradation waits out `progressDeadlineSeconds: 600` while a sync failure resolves once the `retry` budget is spent. Both triggers now confirmed to fire on the condition they name, and only on it.

**An alerting gap turned up by accident.** The first attempt used `git commit -am`, which stages only *tracked* files — so the manifest was never committed while the line referencing it was. That produces a `ComparisonError`, and **neither trigger fires on it**: `on-sync-failed` reads `operationState.phase`, which never changes because no sync is attempted; `on-health-degraded` reads health, which stays `Healthy` because the last-known-good workload is still running. A config repo that has stopped rendering looks identical, to the notification layer, to one with nothing to do — and forgetting to `git add` is a likelier real-world mistake than a bad image tag. `GITOPS.md` carries the third trigger that closes it, left unadded because the rubric names exactly two.

The earlier `on-health-degraded` run is still the record for that trigger — a deliberate bad merge (`uptimecrew/taxcalc-api:0.0.0-does-not-exist`) drove `taxcalc-api-dev` to `Degraded` at `19:27:48Z`:

```
19:27:50Z info   Trigger on-sync-failed result:      [{... [app-sync-failed]     false}]  resource=argocd/taxcalc-api-dev
19:27:50Z info   Trigger on-health-degraded result:  [{... [app-health-degraded] true }]  resource=argocd/taxcalc-api-dev
19:27:50Z info   Sending notification about condition 'on-health-degraded...' to '{slack taxcalc-deploys}'
19:27:50Z error  Failed to notify recipient {slack taxcalc-deploys}: Post "https://slack.com/api/chat.postMessage": ...
```

Four things are confirmed there without a working webhook: the trigger evaluated **true** on a real degradation, the `team=taxcalc` subscription selector matched and resolved the recipient, the controller attempted delivery, and **`on-sync-failed` correctly stayed `false`** — the sync itself succeeded and only health degraded, so the two triggers discriminate rather than both firing on any bad news. Rollback was `git revert` on the config repo and nothing touched the cluster: `Healthy` again at `19:30:29Z`, **13 seconds** after the revert reached `main`.

**That error message also exposes a bug in the spec's own instructions.** It says to create the secret as `--from-literal=slack-token=$SLACK_WEBHOOK_URL`. `service.slack` is the Slack **API** integration — the controller sends `slack-token` as a bearer credential to `https://slack.com/api/chat.postMessage` and takes the channel from `recipients: [slack:taxcalc-deploys]`. A webhook URL put there is never requested as a URL at all; it is sent as a token and rejected. The URL in the error above is the whole finding — it is not the URL that was supplied. An incoming webhook needs `service.webhook.<name>` with a `url:` field and a matching `recipients:` entry, which is a different service type entirely.

### Running the image CI actually pushed — two blockers, and what emulation costs

`overlays/dev` pins `ghcr.io/ai-native-2026-07-29-intuit/taxcalc-api:ec36057e…`, digest `sha256:b1b3c2e7…`, byte-identical to what the GitHub packages API reports for that tag. Getting there meant going through two independent blockers:

**The GHCR package is private** and org policy blocks changing package visibility (recorded on W6 D1). A real cluster cannot pull it without an `imagePullSecret` holding a long-lived PAT — which sits badly beside the OIDC premise the pipeline just established. The k3d lab sidesteps it by **side-loading**: pull on the host where Docker is already authenticated, `k3d image import` into the node stores, and the kubelet never contacts a registry.

**CI publishes `linux/amd64` only, and the k3d nodes are `arm64`** — so the image would not run even if the package were public. It runs here only because the Rancher Desktop VM has binfmt registered; verified with a throwaway pod *before* committing, not assumed. **This affects every engineer in the cohort running k3d on Apple Silicon, and it is invisible until a pod refuses to start.**

**Emulation is not free, and the number is bigger than "a bit slower."** Same application, same manifests, **both at rest** (three samples 20s apart, after the HPA had settled):

| environment | image | CPU (steady state) | memory |
|---|---|---|---|
| `taxcalc-dev` | amd64 CI image, emulated | **56–62m** | 796Mi |
| `taxcalc-staging` | arm64 local build, native | **6–8m** | 479–497Mi |

Roughly **8–9× the idle CPU** and ~1.6× the memory for identical work.

**The first version of this table said 14×, and it was wrong** — those samples were taken while the rollout was still settling, so they measured the startup spike rather than steady state. Re-measured at rest. A resource measurement taken during a rollout is a measurement of the rollout.

The spike is real and visible: it pushed dev past the HPA's 70%-of-request target and the autoscaler scaled 1 → 2, then back to 1 about **390 seconds** later — W5 D3's 300s `scaleDown` stabilization window plus a reconcile. So **`1/1` does hold at rest**, and Task 1's two checks are not in permanent conflict; they simply cannot both be observed in the ~6 minutes after a deploy. It also matters downstream: **W6 D5 gates a canary on W5 D5's p99 latency SLI**, and a p99 measured against an emulated binary says nothing about production. The fix is a multi-arch build in `_build-and-push.yml`, which is not free — the Dockerfile's `builder` stage runs Gradle and `jlink-builder` builds a custom JRE, both slow under QEMU. Recorded as a trade-off rather than smuggled into this deliverable.

### The weekend sync window fired for real, and prod needed the escape hatch

This work ran on a **Friday after 17:00 UTC**, so the AppProject's `syncWindows` deny block (`schedule: "0 17 * * 5"`, `duration: 60h`) was live. `taxcalc-api-prod` came up `OutOfSync` / `Missing` with `SyncWindow: Manual Allowed` and `Assigned Windows: deny:0 17 * * 5:60h`, and reached `Synced` only through the window's documented `manualSync: true` escape hatch. A guardrail that has actually refused something is worth more than one that has only been read.

### Guardrails are verified, not asserted

`kubectl get appproject taxcalc -o yaml` proves the YAML says the right words; it does not prove the controller enforces them, and those are different claims — Argo CD validates `destinations` and `sourceRepos` when an Application spec is written, but the resource allow-lists only at **sync** time. The config repo's `scripts/verify-appproject-guardrails.sh` asserts five deny paths plus a positive control, **6 passed, 0 failed**:

```
PASS  destinations: kube-system refused -> application destination server
      'https://kubernetes.default.svc' and namespace 'kube-system' do not
      match any of the allowed destinations in project 'taxcalc'
PASS  sourceRepos: argocd-example-apps refused
PASS  resource allow-list: Namespace refused
PASS  resource allow-list: ResourceQuota refused
PASS  resource allow-list: LimitRange refused
PASS  positive control: taxcalc-api-dev is Synced/Healthy
```

The positive control is what stops the script passing by refusing *everything*: a deleted project, an empty `destinations` list or a typo'd `sourceRepos` would otherwise score 5/5 and look perfect. **Validated against a known-bad input rather than trusted because it printed green** — run against a scratch AppProject with `'*'` everywhere (referenced by no Application, so it grants nothing), all five deny checks FAIL and only the control passes. The real project is never widened to test it; a guardrail routinely disabled for testing is not a guardrail.

**That negative-control run also found a genuine bug in the verifier itself, which is the strongest argument for writing one.** Checks 3–5 work by syncing a scratch Application at `platform/` and reading the per-resource refusals — and when the project does *not* deny those kinds, the sync **succeeds** and everything under the path is really applied. It applied the committed placeholder Secret over the out-of-band password and broke Postgres auth in `taxcalc-dev`: a verification script damaging the thing it was verifying. Everything directly under `platform/` is now idempotent, and the one destructive file moved to `platform/secret/`, out of reach of `directory: { recurse: false }`.

### Installing Argo CD behind a TLS-intercepting proxy — two failures, neither in any tutorial

**Every pod sat in `ImagePullBackOff`.** The k3d nodes could not verify `quay.io`'s certificate (`x509: certificate signed by unknown authority`) because the corporate proxy (Zscaler) re-signs TLS and the node image carries no such root CA; the host Docker daemon does trust it. Pulling on the host and `k3d image import`-ing was **not sufficient on its own**: Argo CD's install manifest sets `imagePullPolicy: Always` on every container, so a pre-seeded node image store is ignored entirely. Patching the seven workloads to `IfNotPresent` is what actually started them. Separately, `k3d image import -c <cluster>` imported into the *server* node only here — the two agents needed explicit `-n` flags, and the pods that stayed broken were exactly the ones scheduled on agents.

**Then the repo-server could not clone from GitHub**, and the same `x509` error surfaced as an Argo CD `ComparisonError` rather than as anything that looks like a TLS problem:

```
Failed to load target state: failed to generate manifest for source 1 of 1:
rpc error: code = Unknown desc = Get "https://github.com/.../info/refs?service=git-upload-pack":
tls: failed to verify certificate: x509: certificate signed by unknown authority
```

Fixed by putting the proxy's CA chain into `argocd-tls-certs-cm` keyed by hostname (`github.com`) and restarting the repo-server — Argo CD's own mechanism for this, and preferable to `insecure: true` on the repository, which disables verification rather than supplying the missing trust anchor.

### Where this deliverable departs from the letter of its spec

**No `taxcalc-api/` subdirectory, again** — [`GITOPS.md`](GITOPS.md) is at the repository root, same reasoning as `.github/PIPELINE.md` and `docker/SECURITY.md`.

**There was no `deploy-k8s` job in `ci.yml` to replace.** The cluster-touching job has always lived in `k8s-ci.yml`, and it builds its **own ephemeral k3d cluster inside the runner** rather than authenticating to a standing one — so there were never cluster credentials in this repository to remove. `grep -RIn 'kubeconfig\|KUBECONFIG' .github/` returned **zero before** this deliverable and returns zero after it. `k8s-ci.yml` stays as a manifest-validation gate.

**`manifests/` now has a second copy in the config repo's `k8s/taxcalc-api/`, and two copies can drift.** This is a real, accepted gap rather than an oversight: the migration is to delete `manifests/` and point `k8s-ci.yml` at the config repo, which is out of scope for today and recorded in `GITOPS.md` rather than left for someone to find.

**No `Namespace`, `ResourceQuota`, `LimitRange` or `Secret` in `k8s/taxcalc-api/` — and the first is a contradiction in the spec, not a preference.** The reference layout wants `00-namespace.yaml` copied into `k8s/taxcalc-api/` **and** `clusterResourceWhitelist: []`. Those cannot both hold: under the empty whitelist the Namespace is refused whether it arrives as a manifest (`resource :Namespace is not permitted in project taxcalc`) *or* as `CreateNamespace=true`'s injected resource — see the finding below. The grading rubric names `clusterResourceWhitelist: []` explicitly, so that is the instruction kept and the Namespace moves to `platform/`. The quota knobs are on the `namespaceResourceBlacklist` by the spec's own instruction; the Secret is finding 1. The reference's `namespaceResourceWhitelist` also lists `Namespace`, which does nothing — Argo CD classifies scope from the API server's discovery data, not from whether a manifest carries a `namespace:` field.

**`Ingress` was added to the `namespaceResourceWhitelist`**, which the reference list omits. The W5 D3 manifest set contains one, so omitting it would deny a resource this project's own base ships. An allow-list has to match the manifests it governs.

**Sync waves are set with per-resource patches, not `commonAnnotations`.** `commonAnnotations` stamps *every* resource with the same number, and a wave every resource shares orders nothing.

**The `1f1f1f1f…` image-tag placeholder was rejected.** The overlays commit a real tag (`0.2.0`) that exists in the cluster's image store. A placeholder resolves to `ImagePullBackOff` and leaves the Application permanently `Degraded` — which destroys the signal Task 4's deliberate-failure experiment is trying to produce, since a caused failure becomes indistinguishable from ambient noise. `kustomize edit set image` replaces whatever is there, so a real tag serves the CI-rewrite purpose identically.

**`SPRING_PROFILES_ACTIVE` is `k8s,<env>`, not a bare `<env>`.** `application.yml` defines documents only for `docker`, `k8s` and `test`, and the `k8s` document supplies the in-cluster datasource, Redis, Mongo and Kafka coordinates. A bare `dev` would leave the app on its localhost defaults and it would never start. Likewise the overlays patch `LOGGING_LEVEL_ROOT` (which Spring Boot's relaxed binding maps to `logging.level.root`), not the reference's `LOG_LEVEL`, which nothing reads.

**`argocd` CLI v2.11.7 is pinned to a local path**, matching the server. Homebrew installs v3.5.x, which is a different major line from the pinned server; the seven artefacts were verified with the matching client.

**The `argocd-author` Claude Skill was not available in this session's tool listing** — the second deliverable running into that, after W6 D1's `github-actions-author`. The artefacts were hand-authored against the cohort checklist with its three named quirks audited explicitly; `GITOPS.md`'s final section carries the audit, including the one suggestion accepted (`syncOptions` in full — `ServerSideApply=true` earned it immediately by letting Argo CD adopt W5 D3's client-side-applied resources with no field-manager conflict) and the one rejected (the image-tag placeholder).

## Week 6 Day 3 — AWS Fundamentals & CloudFormation Substrate

W6 D1 shipped a pipeline that federates into AWS over OIDC; W6 D2 handed deploys to Argo CD pulling from a config repo. Both assumed a substrate — an account, a VPC, an artefact bucket, a role with something to deploy *into*. Today authors that substrate as raw-YAML CloudFormation: four stacks under `cfn/` in the [config repo](https://github.com/AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability-config), a CI gate that lints and scans them, and [`taxcalc-api/INFRA.md`](https://github.com/AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability-config/blob/main/taxcalc-api/INFRA.md) carrying the full write-up. This section is the summary and the deviations.

Everything lands in the config repo, on `w6d3-implementation` — the templates describe infrastructure the config repo's own CI validates, so that is where they live. This repo gains one file: `.claude/skills/cfn-author/SKILL.md`, the locally-authored generator for the audit pass (see below).

**The substrate, split along blast-radius lines rather than by convenience:**

| Stack | Holds | Lifetime |
|---|---|---|
| `taxcalc-bootstrap-dev` | Artefact bucket for `aws cloudformation package` + `role/taxcalc-api-cfn-deploy`, the OIDC role every later deploy assumes | deployed once by a human with admin |
| `taxcalc-artifacts-dev` | The hardened S3 artefact bucket for SAM builds and Argo CD config snapshots | rarely |
| `taxcalc-network-dev` | 3-AZ VPC, 6 subnets, IGW, 1–3 NAT GWs gated by `EnvName`, route tables, app SG | long-lived; rebuilding churns every subnet id in the account |
| `taxcalc-app-dev` | RDS Postgres + subnet group + DB SG + Secrets Manager master credentials, consuming the network via `!ImportValue` | changes most often |

**Nothing is deployed to AWS, and that is a credentials gap rather than a scoping decision.** No account is wired to either repo — `aws sts get-caller-identity` returns `NoCredentials`, and `vars.AWS_ACCOUNT_ID` is unset. (The `floci` profile in `~/.aws/credentials` is *not* an expired AWS key, as first assumed; it is the emulator's dummy credential and works perfectly against `AWS_ENDPOINT_URL=http://localhost:4566`.)

**So all four stacks were deployed against [floci](https://github.com/floci-io/floci) 2.0.1 instead** — the same local emulator W5 D4 and W6 D1 used for exactly this gap. Endpoint only; no parameter or command changed. All four reached `CREATE_COMPLETE` through the real `create-change-set → describe-change-set → execute-change-set` flow, and **all four Done-When commands now return the required answer** — but three of them for a reason that will not be the reason on real AWS, which is the subject of "Three blocked Done-Whens, closed" below. **`INFRA.md` says which engine produced every row of evidence.**

What floci genuinely established: the `Conditions` gate from **both** sides of one template (dev ChangeSet 22 resources, `EnvName=staging` 30, the diff exactly the HA NAT set); `!Cidr [VpcCidr, 6, 8]` producing six /24s from `10.41.0.0/24`; the cross-stack SG pairing end to end (DB SG `UserIdGroupPairs GroupId` == the network's exported `AppSgId`, `IpRanges: []` — SG membership only, no CIDR fallback); the 32-char generated password **absent** from `get-template`, `describe-stacks` and `describe-stack-events` while the stored template holds only the `{{resolve:}}` directive; and `Replacement: False` on an UPDATE ChangeSet.

### floci's most confident answer was its wrongest — again

**It deleted `taxcalc-network-dev` while `taxcalc-app-dev` was importing three of its exports.** Real CloudFormation refuses outright (`Export taxcalc-network-dev-PrivateSubnets cannot be deleted as it is in use by taxcalc-app-dev`); floci performed the delete and then reported `0` matching exports. It does not merely fail to verify the safety `Export.Name` buys — **it demonstrates the opposite one**, and an engineer who trusted it would conclude `!ImportValue` protects nothing. This is the W6 D1 forged-token result in a new costume: the emulator is a good CFN *engine* and a poor CFN *service* — it models resources well and the control-plane guarantees around them barely at all.

Two smaller gaps, both isolated rather than assumed. **`validate-template` is a stub**: it returns empty `Parameters` for templates declaring 2–5 of them, and passes a template with a fictional resource type, a dangling `!Ref` and a malformed `!GetAtt` that `cfn-lint` rejects with `E3006` — which is the argument for leaving cfn-lint and cfn-nag ungated in CI and letting only `validate-template` skip. `detect-stack-drift` and its two companions return `UnknownAction`, so the drift Done-When has no local path at all.

### Three blocked Done-Whens, closed — and what each one is actually worth

The first pass left three of the four Done-When commands unanswerable and said so. All three now return the required answer on floci, and the write-up is worth more for *how* they differ than for the fact that they pass.

**1. The app stack reaches `CREATE_COMPLETE` on the committed template — the one that genuinely closes.** The blocker was floci refusing to resolve a bare `!Split` into a list-typed property, and the first pass responded by leaving the template alone and verifying the rest with an uncommitted probe that substituted a literal list. That made the status table claim a `CREATE_COMPLETE` no shippable template had earned. There is a fourth spelling — `!Select [n, !Split [",", !ImportValue …]]` per element — which is a real YAML list of scalars rather than one function returning a list, is equally valid CloudFormation, hardcodes nothing, and reads from the same export the task names. The committed template now uses it and reaches `CREATE_COMPLETE` with both imports resolved to concrete ids (subnet group == the three exported subnet ids; DB SG ingress == the exported `AppSgId`, `IpRanges: []`). The cost is that the element count is pinned at three, which is commented at the property.

**2. The two S3 Done-Whens pass in the data plane, not through CloudFormation.** The phantom-policy finding was originally written up as "floci does not support this". That was wrong in a way worth correcting: `put-public-access-block`, `put-bucket-encryption` and `put-bucket-policy` all work against floci's S3. What is broken is floci's *CloudFormation provider* for those resources — it accepts the properties, reports `CREATE_COMPLETE`, mints a plausible physical id for the policy (`bucket-policy-80a48155`), and never calls S3. One layer narrower than "unsupported", and that layer is shimmable: `cfn-guardrails.sh reconcile-s3` reads all three settings **out of the template** (`cfn-extract-s3.rb`, so they cannot drift from what a reviewer approved) and applies them over the S3 API. It refuses to run against real AWS, where CFN applies them itself and reaching around it would register as drift. Writing it turned up a genuine CFN/S3 naming difference — the template says `ServerSideEncryptionByDefault`, the API wants `ApplyServerSideEncryptionByDefault` — which had produced a false PASS until a from-clean re-test caught it.

**3. The delete is refused by CloudFormation — by termination protection, which is not the guarantee the task is about.** floci has no export-in-use enforcement and cannot be made to have one. It *does* implement termination protection, so `delete-stack` on `taxcalc-network-dev` now returns a real `ValidationError` from the DeleteStack API and the stack survives with all six exports. That is more than a shell script refusing, and it is worth having on a long-lived network stack anyway. It is still the wrong shape: export-in-use knows *why* it refuses and lifts itself when the last importer goes, termination protection knows neither. `INFRA.md` tabulates the difference rather than letting the green tick imply the stronger claim.

**The honest summary: one of the three is a fix, two are stand-ins that make the command return the right answer for the wrong reason.** Each is labelled that way at the point of evidence. `cfn-guardrails.sh` check 7 now prints a note saying a live bucket policy on this endpoint is most likely `reconcile-s3`'s doing rather than CloudFormation's — without it, the suite would quietly start certifying the exact failure it was written to catch. Full suite against floci: **10 passed, 0 failed, 7 parity gaps**, up from 6/0/11.

**One thing the rebuild demonstrated for free:** `DeletionPolicy: Retain` on an in-stack secret is a trap on recreate. Tearing the app stack down left `taxcalc/dev/db-master` and `taxcalc-dev` behind, and the next create failed with `DB instance taxcalc-dev already exists` — an error that names neither the retain policy nor the fix. That is the cost of creating the master secret inside the stack instead of out-of-band as the task specifies, and it is the same root cause as the `DependsOn: DbMasterSecret` finding rather than a second independent one.

**What did run, on all four templates:** `cfn-lint` 1.56.1 with the `cfn-lint-serverless` rule pack → **0 findings**; `cfn_nag_scan` 0.8.10 `--fail-on-warnings` → **0 failures, 0 warnings**. Plus a cross-check that every `!ImportValue` in the app stack resolves to an export the network stack actually declares.

### What changed after this section was first written

All four stacks now reach `CREATE_COMPLETE` on floci and stay there — every gap below was closed with a local workaround rather than left as a caveat, and each is measured, not assumed:

- **The `Fn::Split`-into-`SubnetIds` failure is fixed in the template**, not worked around: `!Select [n, !Split [...]]` per element is a real list of scalars, equally valid CFN, reads the same export, hardcodes nothing.
- **`cfn-lint-serverless` was wired, and caught itself being broken.** The first attempt (`cfn-lint==1.22.3` + the pack) loaded silently and reported zero findings — indistinguishable from correctly-wired-and-clean. Caught with a scratch SQS queue that should have tripped `ES6000` and didn't. The pack needs `cfn-lint>=1.44.0`; bumping to `1.56.1` made the same probe fire correctly.
- **`taxcalc-network-dev`'s CREATE_COMPLETE and Task 4's UPDATE-ChangeSet requirement were decoupled.** Task 4's literal ask (rename a tag, confirm no replacement) never had to be the DB-SG-tightening design; the tightening stays fully built and verified, just not applied to the one stack Task 2 grades against.
- **PAB, encryption and the bucket policy are real, live S3 state** via a small reconciliation step — floci's CloudFormation provider reports the bucket `CREATE_COMPLETE` without ever calling the S3 API for those three settings; the fix reads them from the template and applies them directly, and refuses to run against a real account.
- **A working stand-in for the missing drift API**, using `VersioningConfiguration` (a property floci's S3 API genuinely stores) as the mutated property instead of a tag (which floci's CloudFormation provider never applies to anything).
- **The delete refusal is real now, for a different reason than the task describes**: termination protection, not export-in-use enforcement — floci has none of the latter and cannot be given any, so the distinction is documented rather than let a green check imply the stronger guarantee.
- **`Replacement: False` is reported correctly and still not honoured on execute** — reproduced a third time on a disposable clone stack, never the graded resource.

Full detail, live commands and current results: `taxcalc-api/INFRA.md` and [config#10](https://github.com/AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability-config/pull/10).

### Five things the tools or the reading caught — each one changed the YAML

**1. `cfn-nag` found a real failure, not a warning: the DB security group had implicit allow-all egress.** Specifying only `SecurityGroupIngress` is not "no egress" — CloudFormation restores the default allow-all rule, so the database could open outbound connections anywhere on the internet. `F1000`. The fix is awkward enough to be worth recording: CFN treats `SecurityGroupEgress: []` as *unset* and restores the default, so "no egress" has no direct spelling; the narrowest expressible rule (tcp/5432 to `127.0.0.1/32`, unroutable from the ENI) is inert by construction while still being a rule, which is what displaces the default.

**2. The app SG that enumerates only 443 egress cannot reach its own database — and no linter says so.** Enumerating *any* egress replaces the default allow-all. A VPC, an RDS instance and a security group set that all deploy cleanly, report `CREATE_COMPLETE`, and cannot talk to each other. The failure is an *absence*, and neither `cfn-lint` nor `cfn-nag` has an opinion about absences that are legal. This is the strongest single argument in the deliverable for reading generated infrastructure rather than deploying it.

**3. `{{resolve:secretsmanager:...}}` does not create a dependency, so the RDS instance needs an explicit `DependsOn`.** CloudFormation does not parse dynamic references when building its dependency graph — the resolve string is an opaque literal. Without `DependsOn: DbMasterSecret` the instance and the secret are free to be created in parallel, and the deploy fails *intermittently* on a secret that does not exist yet. Intermittently is the bad part: it passes in dev and fails in prod.

**4. The reference workflow's `configure-aws-credentials` pin is a 42-character string.** A git SHA is 40. `gh api repos/aws-actions/configure-aws-credentials/commits/<it>` → `HTTP 422, No commit found`. The workflow would fail at step setup on every run, forever. All three action pins in the committed `cfn-validate.yml` were checked against the GitHub API before commit. A pinned SHA is only as good as the one check nobody runs.

**5. `cfn-nag` 0.8.10 cannot run on Ruby 4.** It pulls `kwalify` 0.7.2, which calls `StringScanner#peep` — removed in Ruby 4.0. On 4.0.6 the scan dies in the require chain with a `NoMethodError` before it reads a single template; on 3.3 the identical templates return 0 findings. `ruby-version: "3.3"` in CI is a pin with a reason attached, not a default.

### Deviations from the reference layout

**`runs-on: blacksmith-2vcpu-ubuntu-2204`, not `ubuntu-24.04`.** GitHub-hosted runners have not started in this org since the W5 D2 Actions billing block; a job pinned to a hosted label dies in ~3s with "recent account payments have failed" — a runner that never booted, not a lint failure. Every other workflow in this repo already uses the Blacksmith label.

**The AWS half of `cfn-validate.yml` is guarded on `vars.AWS_ACCOUNT_ID`,** matching the pattern `deploy-prod.yml` and `serverless.yml` already use here. `configure-aws-credentials` can do nothing with an empty `role-to-assume`, so an unguarded step puts a permanent red X on `main` — and a required check that is always red gets bypassed, which is worse than not having one. `cfn-lint` and `cfn-nag` always run.

**The app SG's 5432 egress is CIDR-scoped, not SG-to-SG.** Taken literally the task text asks for a **cross-stack cycle**: the network stack would import from the app stack, which already imports `AppSgId` from the network stack. The usual escape — an `AWS::EC2::SecurityGroupEgress` declared in the app stack against the imported SG — breaks the cycle but leaves the *network* stack permanently `DRIFTED`, which would make Task 4's `detect-stack-drift` unable to read `IN_SYNC` ever again. The tight direction is enforced where it costs nothing: the DB SG's ingress is `SourceSecurityGroupId`, so SG membership is the credential and a box merely sitting in the same subnet range still cannot open a Postgres connection.

**`MapPublicIpOnLaunch` removed rather than suppressed.** "Public" here means IGW-routed, not auto-addressed — the only things in those subnets are NAT Gateways, which carry their own Elastic IPs and ignore the flag. Suppressing `cfn-nag` W33 would have kept the exposure and hidden the warning.

**`EngineVersion` is a Parameter, not the reference's hardcoded `"16.3"`.** AWS deprecates RDS minor versions on its own schedule; as a literal, that day costs a template edit and a PR for a value unrelated to the change being shipped.

**Explicit resource names kept only where load-bearing.** `CfnDeployRole` keeps `RoleName` (Actions must name the role in `role-to-assume` before the stack can be queried) and `DbInstance` keeps `DBInstanceIdentifier` (it is the handle every operational path uses). The app security group **lost** its `GroupName` — consumers import the SG *id*, so a generated name costs nothing and keeps it replaceable in place.

**`cfn-lint-serverless` is not wired up, though the task text asks for it.** That pack adds Lambda, API Gateway and SAM-transform rules; `cfn/` holds a VPC, an RDS instance, two S3 buckets and an IAM role, and no `Transform` of any kind. It becomes correct in W6 D4 when the LLM cost-monitoring Lambda stack lands — and that is the change that should add it, so the dependency arrives with the first template that justifies it.

**Every `cfn-nag` suppression is written at the resource** in `Metadata.cfn_nag.rules_to_suppress` with its reasoning, rather than as a CI deny-list. A suppression is an argument, and it belongs in the diff where a reviewer can disagree with it. There are six; `INFRA.md` tabulates all of them.

**`taxcalc-api/INFRA.md` *does* take the subdirectory that `GITOPS.md` refused.** The W6 D2 argument was that this repository *is* `taxcalc-api`, so a directory named after it nests every path for nothing. `INFRA.md` lives in the **config** repo, which is not `taxcalc-api` — there the subdirectory names something real.

### The `cfn-author` Skill — third deliverable running into the same gap

`cfn-author` was **absent from this session's skill listing**, exactly as `argocd-author` was for W6 D2 and `github-actions-author` for W6 D1. It was authored locally at `.claude/skills/cfn-author/SKILL.md` and run:

```
/cfn-author taxcalc --region us-east-1 --env dev --vpc-cidr 10.41.0.0/16 --out .cfn-author-out/
```

Output is on the config repo's `scratch/cfn-author` branch, never merged. **The provenance caveat is repeated wherever the audit is cited:** a generator written by the same author as the artefacts under review will tend to agree, the pass was not cold, and a clean diff would therefore have been evidence of nothing.

One measurement does not depend on that caveat, because both trees went through the same two tools:

```
generated   cfn-lint 0 errors;  cfn_nag_scan 1 FAILURE, 11 warnings
committed   cfn-lint 0 errors;  cfn_nag_scan 0 failures,  0 warnings
```

**The generated set does not pass this repo's own CI gate.** Five substantive deviations, all resolved in favour of the committed templates — findings 1–3 above, plus `MapPublicIpOnLaunch` and the hardcoded `EngineVersion`. The skill's three named quirks (`StringLike` on the OIDC `aud` claim, `NoEcho: true` on a password Parameter, `DeletionPolicy` without its `UpdateReplacePolicy` partner) were **all recorded as *not observed* rather than manufactured** — they are in the skill's own non-negotiables, so a generator following it will not commit them, which is exactly why their absence is weak evidence and is reported as such.

## Week 6 Day 4 — Cost Governance & a Governed LLM Feature

W6 D3 built the substrate and taught that the NAT gateway is the silent budget killer. It did not
measure or guard that spend. This day does — and adds the first feature in this application that
spends real money on every request, which turns out to be invisible to every guardrail the day
also builds. Full detail in
[`taxcalc-api/COST.md`](https://github.com/AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability-config/blob/main/taxcalc-api/COST.md),
which lives in the [config repo](https://github.com/AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability-config)
beside `INFRA.md` and beside the `cfn/` templates and static checks it governs — this repo's
[`COST.md`](COST.md) is a pointer to it. What stayed here is the LLM plane's implementation, which
AWS billing cannot see and no CloudFormation template can govern.

**Two spending planes, and only one is visible to AWS billing.** The AWS-resident plane (NAT, RDS,
S3) gets a tag-scoped `AWS::Budgets::Budget` plus an account-wide `EstimatedCharges` alarm, both
publishing to one SNS topic. The Anthropic plane gets neither, because it *cannot*: Anthropic bills
the Anthropic workspace, so no Budget, Cost Explorer report or billing alarm will ever show it. It
is capped at the platform (a Console workspace spend limit) and attributed in-app (a per-request
EMF cost log plus an `X-Cost-Usd` header). A third case makes the axis clear — the self-hosted
embeddings service is an "AI feature" whose marginal cost per call is zero, because it runs on the
cluster's own CPU. "Is it AI?" is the wrong question; "who is the merchant?" is the right one.

### The real API call paid for itself immediately

`AnthropicCostPathLiveIT` calls the live API rather than a stub, and the first run exposed a bug
no stub could:

```
model=claude-haiku-4-5  resolved=claude-haiku-4-5-20251001  in=12 out=4  X-Cost-Usd=0.00005
```

The request names a floating **alias**; the response names the dated **snapshot** that served it.
Pricing off the response id — the obvious reading of "the model that served the call" — throws
`no price for model claude-haiku-4-5-20251001` on the first real request, and every unit test
stays green, because a stub echoes back whatever it was handed. `UpstreamResponse` now carries
both: the alias is the pricing key, the snapshot is logged so a cost line can still be reconciled
against an invoice after the alias floats.

### Three places the reference implementation fails while looking healthy

1. **An unpriced model costing `0.00` instead of throwing.** A silent zero produces a cost log and
   a header that look perfectly fine while reporting a paid call as free, and nothing downstream
   can distinguish "free model" from "unknown model".
2. **`Double.toString` for the `X-Cost-Usd` header.** Java switches to scientific notation below
   `1e-3`, and a real Haiku call costs ~$0.0002 — so the reference spelling emits `2.0E-4`, which a
   k6 threshold or an `awk` pipeline reads as near-zero rather than rejecting.
   `CostResponseHeaderTest` asserts both spellings side by side so a revert is a test failure.
3. **String-concatenated EMF.** `tenant` is caller-influenced; one quote produces malformed JSON,
   and CloudWatch answers malformed EMF by dropping the metric and keeping the text — the cost
   series quietly reads low with nothing failing anywhere.

Cost is carried as an integer count of 1e-5 USD, a **deliberate departure from `CLAUDE.md`'s
scale-2 money rule**, documented at the class. That rule is right for tax liability, where scale 2
*is* the domain; per-call LLM cost sits four orders of magnitude below it, so at scale 2 every call
rounds to `0.00` and a million of them still round to zero. The rule's intent — never accumulate
money in floating point — is kept harder than the letter: `BigDecimal` at scale 8, rounded once,
then integers, which sum without error.

### The ingest path, and the primary key that carries the taxpayer

A schema, an HNSW index and a TEI client are three parts of a feature, not a feature: until
something joins them, no request can put a vector in the table and the whole stack is reachable
only from tests. `TaxpayerEmbeddingIngestService` is that join — taxpayer record → text →
in-cluster `/embed` → `taxcalc.taxpayer_embeddings` — and `TaxpayerEmbeddingController` puts it
behind two routes: `POST /api/v1/taxpayers/{id}/embedding` (write scope) and
`GET /api/v1/taxpayers/{id}/similar` (read scope), the latter being the only production caller of
the index the migration creates.

The interesting constraint is the table the task specifies: four columns, none of them a taxpayer
id. So the **primary key has to carry that link itself**. The row id is
`UUID.nameUUIDFromBytes("tenant|taxpayerId")` — a name-based v3 UUID, same input, same id, on every
node — which buys three things a random v4 id would not: re-embedding a taxpayer rewrites one row
(the INSERT is `ON CONFLICT (id) DO UPDATE`) instead of leaving a stale vector that
nearest-neighbour search keeps returning alongside the new one; a taxpayer's row can be found again
without a lookup table, which is what the `similar` route queries with; and the tenant is part of
the derivation, so the same taxpayer id under two tenants is two rows rather than one tenant
silently overwriting the other's vector.

`similar` queries with the **stored** vector rather than re-embedding the text. Beyond saving a
model call, that avoids comparing vectors produced by two different models if the model is ever
swapped mid-life — which is not slow, it is meaningless.

A second controller rather than two more methods on `TaxpayerController`: that class already
carries five collaborators, and every constructor call and `@WebMvcTest` slice naming it would have
had to grow a mock for the embeddings stack to add a route that shares nothing with the LLM and
idempotency paths. Neither route is behind `RateLimitFilter`, unlike the two LLM routes — that
filter is a cost control on calls that bill an external party per token, and these calls reach a
model on the cluster's own CPU, where the honest control is capacity.

### Three bugs that passed in isolation and failed in aggregate

The pgvector work (Task 3) produced the most instructive failures of the day, all of the same
shape — invisible when you run the class you are editing:

- **`CREATE EXTENSION` landed the type in the wrong schema.** The test harness set Flyway's
  `schemas=taxcalc`, which the application does not, so `vector` became `taxcalc.vector` and all
  five query-side tests failed with `type "vector" does not exist` — while every schema-shape
  assertion still passed. Fixed on both sides: the migration pins `SCHEMA public`, and the harness
  now configures Flyway the way production does. **A test harness configured differently from
  production can manufacture failures, and hide real ones.**
- **A `Connection refused` blamed on `withReuse(true)`, which was not the cause.** 11/11 alone,
  then `initializationError` on the next full run — a `java.net.ConnectException` from Flyway in
  `@BeforeAll`. The first reading was a reaped reuse container leaving a stale entry, and the flag
  was dropped. **Reinstating it disproved that**: the same failure reproduces with reuse *disabled*,
  Testcontainers logging `Reuse was requested but the environment does not support the reuse of
  containers` and Flyway failing anyway, against a container it had just reported started at
  `localhost:34055` — and a forced container restart produced a second container whose port was
  refused just as fast. The real cause is that `PostgreSQLContainer`'s wait strategy watches the
  container **log** for `database system is ready to accept connections`, which says the server
  inside the container is up and nothing about whether the Docker host has finished publishing the
  mapped port; on a VM-backed daemon (Rancher Desktop) that forward is asynchronous and lags under
  full-suite load. That is also why this was the *only* class affected: every other Postgres test
  reaches the database through `@ServiceConnection` and therefore HikariCP, whose pool retries for
  the length of its connection timeout and absorbs the window silently. This one uses a raw
  `DriverManagerDataSource` on purpose — it is testing migrations, not the application's data
  source — and DriverManager either connects on the first try or throws. `migrate()` now retries on
  a connection failure (matched on the cause chain, since Flyway's wrapper message varies by
  version) for ~15s; anything that is not a connection failure is a real schema fault and is
  rethrown on the spot. **The fix is to wait, not to recreate** — and the flag the original
  diagnosis removed was innocent. Worth knowing what reuse is actually worth here, having measured
  it: with `ryuk.container.disabled=true` in this setup, two consecutive opted-in runs each created
  a *new* container anyway, because Testcontainers removes started containers from its own JVM
  shutdown hook when Ryuk is off. The flag currently buys nothing and costs nothing.
- **An index test that asserted table size, not correctness.** At 300 rows Postgres correctly costs
  a sort below an index scan, so the plan was `Sort` and the test failed for its own reasons. It now
  runs with `enable_seqscan`/`enable_sort` off — soft preferences, not overrides, so Postgres still
  falls back when no index can serve — and pairs the `<=>` assertion with an L2 (`<->`) **positive
  control**. Without that control the test would pass just as happily against an index built with
  the wrong operator class, which is the silent failure it exists to catch.

All eight Postgres ITs moved to `pgvector/pgvector:pg16` (stock Postgres has no pgvector;
`IF NOT EXISTS` covers "already created", not "not installed"). That heavier image is also why
`TaxpayerEventFlowIT`'s await budgets are now a named 30s constant: its first assertion failed in
two consecutive full-suite runs while passing every time alone. `OutboxPublisher` sweeps on a 1s
`fixedDelay`, so 5s was five sweeps of headroom against a context cache holding several earlier
ITs open. Widening does not weaken it — those tests pin that a write reaches Kafka through the
outbox, never that it does so within five seconds.

### What the emulator could and could not settle

No AWS account, unchanged since W6 D3, so everything ran against floci 2.0.1. It genuinely settled
the stack's creation, the alarm, and the `IsUsEast1` condition **from both sides** (4 resources in
us-east-1, 3 in eu-west-1). Three parity gaps, each measured:

- **`AWS::Budgets::Budget` reports `CREATE_COMPLETE` against a service that is not running** —
  floci has no `budgets` service at all, yet mints `MonthlyCostBudget-7636191f` and reports success.
  W6 D3's phantom-bucket-policy finding, landing this time on the deliverable's headline resource.
- **The SNS `TopicPolicy`, `KmsMasterKeyId` and tags are silently dropped.** The live topic keeps
  the default `Principal {"AWS":"*"}` policy — **wider** than the committed template, not narrower.
- **`TreatMissingData` is dropped from the alarm**, which is the one property deciding whether it
  fires correctly on a gappy metric.

**Cost Explorer is not emulable, as opposed to unimplemented.** floci's `ce` answers correctly and
returns 36 service groups with every amount `0.0000000000`, and `get-tags` returns nothing at all —
an emulator does not bill anybody, so there is no spend to report and no activated tag to group by.
Unlike the three gaps above, that one cannot be fixed by a later version. The NAT line item can
only be read on a real account; `cfn-guardrails.sh --static` check 5 verifies the *input* to that
report instead, which is an honestly weaker claim.

### The `cost-author` Skill — fourth deliverable, same gap

`cost-author` was **absent from this session's skill listing**, exactly as `cfn-author`,
`argocd-author` and `github-actions-author` were. Authored locally at
`.claude/skills/cost-author/SKILL.md`. The provenance caveat does not weaken with repetition: a
generator written by the same author as the artefacts under review will tend to agree, and **this
pass was not cold** — the cost stack and cost package were written first. One suggestion accepted
(tag the Elastic IPs: attached they are free, detached they bill ~$3.60/mo and a detached EIP is
what a half-finished teardown leaves behind), one rejected (deriving the billing-alarm threshold
from the Budget limit — they measure different things, and coupling them encodes the assumption
that this service is the only thing in the account).

That first pass reviewed the committed artefacts, found none of the three failure modes the task
names, and reported them as "not observed". Honest, and not the deliverable: **a rejection rule
that has never fired is indistinguishable from one that is not wired.** A second pass on the
config repo's `scratch/cost-author` branch therefore ran against three templates authored to carry
the three defects — an untagged NAT Gateway, EIP and RDS instance; `notBreaching` on the billing
alarm; and an `AWS::Budgets::Budget` aimed at Anthropic spend. **16 findings on the candidate, 0
on the committed `cfn/`**, with the first two rejections delegated to the repo's own tracked gate
rather than reimplemented, so what is proved is that the *shipping* check catches them.

The run's one accepted finding was that only two of the three were gated at all: the LLM-budget
rule existed solely as prose. It is now `cfn-guardrails.sh` check 7 — which deliberately does not
match Bedrock, because that is a hosted model *and* AWS-resident spend. The axis is never "is it
AI?" but "who is the merchant?". `taxcalc-api/COST.md` and `.cost-author-out/AUDIT.md` carry the
full record.

### Deviations from the brief

**`POST /api/v1/taxpayers/{id}/explanation`, not `POST /v1/completions`.** The brief's Done-When
assumes a `llmproxy` module with a router and provider adapters from "W3 D1"; this repository's
W3 D1 was *Spring Security 7, JWT Resource Server & Rate-Limited LLM API*, and no such module
exists here. The endpoint follows this application's actual conventions — the existing controller,
its `@PreAuthorize` scope/role pattern, its URL family — rather than inventing a generic completions
route that nothing else resembles. Same for the package: `llm.cost`, not `llmproxy.cost`.

**Migration numbered V5, not V3.** V3 and V4 are the W3 D3 outbox table and the W3 D5 trace-context
column. Flyway keys on version, so a second V3 fails context startup everywhere the real one ran.

**`./gradlew integrationTest`, not `./gradlew :taxcalc-api:integrationTest`.** This is a
single-project build (`rootProject.name = 'tax_liability'`), so there is no `:taxcalc-api` path to
address. The task itself now exists: a filter over the same test source set on the JUnit tag
`integration`, so
`./gradlew integrationTest --tests '*TaxpayerEmbeddingsRepoTest'` runs the container-backed tests
alone. It is deliberately not wired into `check` — `test` already runs them, and adding it would
run every container test twice per build. A separate `integrationTest` *source set* was the other
option and is worse: it needs its own configurations and its own copy of the shared helpers, and
it hides those tests from anything that runs plain `test`.

**`cfn-validate.yml` gained no new validation step.** Every tool in it is already directory-scoped
and `validate-template` already loops the glob, so the cost stack was linted, scanned and
API-validated the moment it was committed. What the generic tools have no opinion about went into
`cfn-guardrails.sh` instead — tag coverage and `TreatMissingData`, both proved to fire by breaking
a template on purpose.

**Four things stand between a clean machine and `deploy/embeddings 1/1`, and each one fails by
naming something other than its cause.** All four are handled, in order, by
[`scripts/embeddings-up.sh`](scripts/embeddings-up.sh) (`--smoke` also POSTs `/embed` and asserts
1024 floats come back). The point of the script is that the thing it finally applies is
`manifests/70-embeddings.deployment.yaml` **unmodified** — everything below is provisioning a real
cluster would already have done:

1. **The weights TEI cannot fetch for itself** (the `hf-hub` redirect bug, below). `curl -L`
   follows the redirect correctly, so fetching out of band works where the pod's own downloader
   cannot — ~1.4GB into `~/tei-models`, mounted into the k3d node at create time.
2. **k3s will not start on a cgroup v1 host.** `failed to validate kubelet configuration ...
   kubelet is configured to not run on a host using cgroup v1` — and the symptom is not that line
   but a node that never registers while `kubectl` says `connection refused` for ten minutes.
   Pinning `rancher/k3s:v1.28.15-k3s1` clears it.
3. **containerd does not trust the intercepting proxy's CA**, though the host's Docker daemon
   does. In-cluster pulls die with `x509: certificate signed by unknown authority`, so images are
   pulled on the host and `k3d image import`ed — the same trick
   [`observability-preload-images.sh`](scripts/observability-preload-images.sh) uses. Three images,
   and two of them are non-obvious: `rancher/mirrored-pause` (without it the error is
   `FailedCreatePodSandBox ... failed to get sandbox image`, which points at the pod's image and
   not the sandbox's) and `mirrored-metrics-server` (without it *every later kubectl command*
   prints several `Couldn't get resource list ... metrics.k8s.io` lines, which look like an error
   in whatever command printed them).
4. **The default StorageClass steals the PVC.** `local-path` is k3s's default, so a claim naming
   no class gets it stamped on by admission and binds to a fresh *empty* directory rather than to
   the volume holding the models. The pod then starts and TEI dies naming a missing model file —
   a binding problem wearing a download problem's clothes. Turning the default off lets the claim
   match [`manifests/dev/70-embeddings-models.localpv.yaml`](manifests/dev/70-embeddings-models.localpv.yaml),
   which is `Retain` so tearing the cluster down never deletes the 1.3GB download.

Result: `kubectl -n taxcalc-dev get deploy embeddings` → `1/1  1  1`, `Available=True`, TEI logging
`Ready` after loading the ONNX graph from disk, `/embed` returning a 1024-dimension vector, and
`EmbeddingsClientLiveIT` green against it — the same test that skips itself when no service is
reachable. The image is amd64-only and runs under emulation on Apple Silicon, which works and is
why the manifest's `startupProbe` is generous; the one oddity observed is TEI exiting 0 and being
restarted once under emulated load, after which it served every request normally.

**The embeddings model is mounted, not downloaded.** TEI's `hf-hub` 0.3.2 disables reqwest's
redirect following and re-implements it by parsing the raw `Location` header as an absolute URL, so
a TLS-intercepting proxy that rewrites that redirect to a relative path fails with
`relative URL without a base`. Confirmed from the shipped crate source; 0.4.3 is byte-identical, so
an image bump does not fix it, and `HF_ENDPOINT` is not an escape hatch (0.3.2 hardcodes the
endpoint). TEI loads from a local directory without contacting the Hub — verified end to end,
`Ready` and `/embed` returning 1024 floats with zero outbound requests. Two traps worth recording:
the CPU build needs `onnx/model.onnx` rather than `model.safetensors` (with only safetensors it
reaches `Starting model backend` then dies naming a missing file), and on macOS a `-v /tmp/…` mount
is silently **empty**, because `/tmp` is a symlink to `/private/tmp` that Docker does not resolve —
which invalidated three of this session's test runs before it was spotted.

## Week 6 Day 5 — Observability, Cost Management & Auto-Scaling

W6 D4 measured what a request costs. This day makes the system react to load — and gates a pull
request on whether it still meets the SLO while doing so. Two autoscalers on the k3d cluster
(KEDA on Kafka lag, an HPA on a custom Prometheus metric), a k6 threshold gate wired into CI, and
an AWS-native pack that is authored and defended rather than deployed. Full write-up in
[`SRE-CAPSTONE.md`](SRE-CAPSTONE.md); the manifests live in the
[config repo](https://github.com/AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability-config).

**The load-bearing change is not the autoscaler, it is one environment variable.** Lag is a
property of a *consumer group*, not of a Deployment. `taxcalc-api` runs the same image as the
worker, so the moment Task 1 deployed a real broker the api pods began consuming
`taxcalc-read-model-builder` — draining the very lag KEDA scales the worker on. Two or three api
replicas keep a dev-rate topic at zero lag however much is produced, and the worker never leaves
`minReplicaCount: 0`. Nothing about that looks like a failure: the ScaledObject is `READY=True`,
the trigger is valid, the broker is reachable, and the read model *is* being updated — by the
wrong pods. It reads as "KEDA isn't working" and sends you to operator logs that are clean. It was
invisible until today only because W5 D3 deliberately deployed no broker at all, just a `kafka`
Service whose selector matched nothing so the bootstrap hostname would resolve.

### Two autoscalers, both measured

`scripts/w6d5-spike.sh` produced 60,000 synthetic `taxpayers.events` records and KEDA drove the
worker `0 → 7 → 0`:

```
18:24:22  worker=1   active=False      # backlog staged, KEDA released
18:24:47  worker=4   active=True       # first poll after release
18:25:12  worker=7   active=True       # peak
18:29:50  worker=7   active=False      # topic drained; cooldownPeriod (300s) begins
18:30:15  worker=0   active=False      # scale-to-zero
```

The first spike proved nothing, and that is worth keeping. One replica drains 6,000 records in
under 20 seconds while KEDA polls every 15s and a JVM pod needs ~40s to start — so the backlog was
gone before a second replica could be justified, KEDA correctly declined to add one, and the run
peaked at 1. **The autoscaler was right and the measurement never happened.** The script now pauses
KEDA at zero, produces, then releases, and 60,000 is sized from the measured drain rate rather
than picked.

The deliverable's own smaller check — ~50 records, scale to ≥1 within one polling interval, drain
back to zero — runs as written, and the reason it works is worth keeping:

```
12:40:50  worker=0   active=False    # 50 records land on a scaled-to-zero Deployment
12:41:05  worker=4   active=True     # +15s: first poll after the produce
12:41:21  worker=5   active=True     # ceil(50/10) = 5
12:46:24  worker=0   active=False    # drained, then cooldownPeriod (300s)
```

Fifty records are invisible to a *running* replica — it drains them faster than KEDA polls — and
unmissable to a *scaled-to-zero* one, because at zero nothing drains and every record stays as lag
until KEDA starts a pod. The size of the produce was never the variable; whether anything was
consuming when it landed was. The spike script now stages the backlog only when the worker is
already running. Running that check also turned up a real bug: `BATCH` was fixed at 1000, so
`COUNT=50` produced a thousand records and logged fifty.

A second surprise on the way there: **an empty topic is not "no lag" to KEDA, it is an *invalid
offset*.** A group that has never committed has nothing to subtract from, and the kafka scaler's
default `scaleToZeroOnInvalidOffset: false` holds the Deployment at one replica rather than zero —
the reasoning being that scaling to zero would mean nothing ever commits and the group could never
recover. A freshly deployed worker therefore sat at 1 replica against an empty topic, which reads
exactly like "KEDA thinks there is work when there is none". `k8s/taxcalc-api/kafka-bootstrap.job.yaml`
now seeds the group at the log-end offset during the sync that creates it, so a fresh deploy rests
at `READY=True` / `ACTIVE=False` / `0/0` without anyone having to run a load generator first.
Flipping `scaleToZeroOnInvalidOffset: "true"` instead would look like the same fix and deadlock
the autoscaler: at zero replicas with an invalid offset, nothing ever joins the group to make the
offset valid.

The HPA moved off CPU, because CPU is the wrong signal here and wrong in the direction that never
fires: `taxcalc-api`'s slowest path is an outbound Anthropic call, and a pod serving one is parked
in a socket read holding a request, a thread and a connection while burning almost no CPU. Under
LLM-bound load p99 goes through the 500 ms objective while utilisation sits in the teens. It now
scales on `taxcalc_inflight_requests`, a Micrometer gauge published by `InflightRequestsGauge` and
routed through the Prometheus Adapter into the custom-metrics API. `averageValue: 6` is measured,
not chosen — a single-replica saturation ramp holds p99 under 500 ms to about six concurrent
requests and breaks above it.

**The actuator scrape must not count itself**, and the cost of getting that wrong is not a rounding
error. `/actuator/prometheus` is in flight at the exact moment it collects the gauge, so counting
it puts a permanent floor of 1 under a metric whose job is to sit at 0 on an idle pod. Against a
target of 6 that is a sixth of the scaling target reported as real load on a completely idle
Deployment — holding replicas up that should come down, most visibly overnight. Verified on a live
pod: `taxcalc_inflight_requests{app="taxcalc-api"} 0.0`.

### The real scaling ceiling was the ResourceQuota

The HPA scaled correctly and the pods never arrived:

```
Normal  SuccessfulRescale  New size: 10; reason: pods metric taxcalc_inflight_requests above target
Warning FailedCreate       forbidden: exceeded quota: taxcalc-dev-quota,
                           requested: limits.cpu=500m, used: limits.cpu=8, limited: limits.cpu=8
```

The Deployment sat at `2/10` and stayed there. **No container in this repo declares `limits.cpu`** —
W5 D3 omitted it deliberately to avoid CFS throttling — so that 500m is the namespace LimitRange's
`default: {cpu: 500m}` applied at admission, exactly as that manifest's W5 D3 note warned. Every
pod silently spends 500m of an 8-CPU quota, the namespace tops out near sixteen pods across *all*
workloads, and `maxReplicas: 20` is unreachable by a factor of five. The HPA reports success, the
Deployment reports `2/10` forever, and the only trace is a ReplicaSet event nobody is watching.
An autoscaler's maximum is a request, not a guarantee.

**Re-measured, and that quota is an *accidental* ceiling rather than the designed one.** The
namespace's deliberate budget is `requests.cpu: 4` on a 4-CPU node; `limits.cpu: 8` binds long
before it, and the 500m each pod spends against it is a LimitRange default multiplied by a
container that declares no limit — two defaults, neither chosen for this purpose. It also does more
than cap autoscaling: with `limits.cpu` at `8/8` a routine **rolling update** stalled at 3 pods,
because the ReplicaSet could not create the replacement. And the two autoscalers **contend for that
one budget**: the k6 mix is 55% writes, every write emits to `taxpayers.events`, so the load test
that exists to exercise the api's HPA also drives KEDA's worker 0 → 12, and those pods claim the
`limits.cpu` the api's new replicas need. Measured: api HPA asked for 5, KEDA asked for 12, the
namespace satisfied neither. Two correct autoscalers, one budget, no arbitration. Applied for dev (`limits.cpu` 8 → 16, `pods` 20 → 40), and the effect is visible: the HPA now
reaches 7 desired / 4 ready, and the refusal has moved to `limits.memory` with `requests.memory`
at `7552Mi/8Gi` on a 13Gi node — the intentional, node-sized budget binding rather than an
accident of two defaults. staging and prod are deliberately untouched, and this still wants
platform-team review: the AppProject blacklists `ResourceQuota` precisely so an app team cannot
raise its own ceiling.

### The 50-VU check, and three quiet failures in front of it

Run as an in-cluster k6 Job at exactly 50 VUs with `SLEEP=0`, the first attempt went `2 → 4` at
**t+42s** with `ready` never leaving 2 — missing the deliverable's ~30s on one half and its
"scales above minReplicas" on the other. Both are now fixed and the same run reads `2 → 4` at
**t+24s**, `ready` 4 at t+69s, then 7 desired.

**The timing fix is the interesting one, because the obvious version of it is wrong.** Three lags
stack: the scrape, the adapter's `avg_over_time` window, and the HPA's 15s control loop. Shortening
the window alone would have hit the number while gutting the thing that makes the signal
trustworthy — `scaleUp.stabilizationWindowSeconds` is `0` by design, so that average is the only
guard against one unlucky scrape. Since noise rejection depends on the **sample count**, not the
window's width, the scrape went to 5s *and* the window to `[30s]`: six samples averaged where there
were four, in half the wall-clock time. Faster and smoother, not a trade. The second fix was the
`limits.cpu` quota below, which had been refusing every replica the HPA asked for.

Three things had to be fixed before that run measured anything, and each failed silently. **The
Prometheus Adapter was `OOMKilled` every ~8 minutes** on a 256Mi limit this deliverable set by eye;
it uses 240Mi at idle because it runs informers over every Pod in the cluster, not because of its
one rule — and while it is down every custom-metric HPA reads `<unknown>` and *holds* its replica
count, so autoscaling stops with no alert and a healthy `0/6` is just a sample taken between kills.
**The loadtest JWTs had expired** (2h TTL, 3h old), so k6 drove 6,128 req/s of 401 while the gauge
correctly read ~0 — the load was not real and the autoscaler looked broken. **And the image tag the
overlays pinned did not contain the gauge at all**: `ghcr.io/…:9d3c9e8b…` was published before W6 D5
added `InflightRequestsGauge`, and `grep -c inflight` against its `/actuator/prometheus` returns
`0` — so every W6 D5 measurement had in fact run on a **local build that was never published**, and
the first two Done-When checks were passing only because a quota-wedged rollout had left two of
those pods alive. Resolved by building from HEAD, verifying `InflightRequestsGauge.class` is in the
image, publishing it as `w6d5-local-acef557` (the `-local-` infix is deliberate: arm64 where CI
publishes amd64, and built with the corporate TLS-interception CA injected into the Gradle stage),
and pinning it. Argo CD now reports `Synced / Healthy` with both pods on the Git-specified image.
`_bump-config.yml` overwrites the tag on merge. See `SRE-CAPSTONE.md`.

### The k6 gate, and the two things that had to exist before it meant anything

214,030 requests over 12 minutes from an in-cluster k6 Job at 200 VUs — all five thresholds green:

```
checks                ✓ 'rate>0.99'      rate=99.90%
cost_per_request_usd  ✓ 'p(95)<0.003'    p(95)=0.00062   <- 1.74x high; see the note below
cost_samples          ✓ 'count>0'        count=10609
http_req_duration     ✓ 'p(99)<500'      p(99)=39.48ms
http_req_failed       ✓ 'rate<0.005'     rate=0.04%
```

**The cost figure above was later found to be 1.74x too high**, and the bug was in `PriceBook`
rather than anywhere in the gate. The table held one *blended* rate per model and the
`claude-haiku-4-5` entry was `0.003`/1K - exactly `(0.001 + 0.005) / 2`, a 50/50 input:output
split, against a real workload of about 82/18. A blended rate cannot be wrong in a way the
arithmetic notices: the multiplication is correct, the EMF line is well-formed, the header is
present and exponent-free. Corrected to separate input and output rates and re-measured on the
same shape: **`p(95)=0.00043` from 9,216 samples**, still far inside the `0.003` budget. The three
SLO numbers never moved.

`X-Cost-Usd` already existed and was already correct from W6 D4 — `BigDecimal.toPlainString()`,
not the reference implementation's `Double.toString`, which renders a ~$0.0002 Haiku call as
`2.0E-4`. What was missing was everything around it:

1. **The load test could not authenticate.** Every route worth testing is behind a JWT, and
   `issuer-uri` points at `https://idp.example.internal` — a placeholder with nothing behind it.
   That does not merely invalidate the numbers, it *inverts* them: 401s are fast, so p99 looks
   superb, `http_req_failed` pins at 1.0, and the cost Trend stays empty because an unauthorised
   request never reaches the cost path. The prettiest latency graph this repo can produce is the
   one where nothing worked. `scripts/loadtest-token.sh` mints real RS256 tokens against a keypair
   whose private half is gitignored and never leaves the machine.

   **Setting `public-key-location` is not enough — `issuer-uri` must be cleared**, which is the
   opposite of what the auto-configuration's structure suggests. The three `JwtDecoder`
   configurations are each `@ConditionalOnMissingBean` and public-key is declared *first*, so it
   reads like it wins. It does not: with both set, the issuer-uri decoder is built and the mounted
   key is silently ignored. The symptom is a flat 401 with a bare `WWW-Authenticate: Bearer`, no
   log line, and no startup error — the decoder is lazy — while the key is mounted, the profile is
   active and the pod is healthy.

2. **The cost path could not be load-tested at all.** 200 VUs for six minutes is ~100k paid
   Anthropic completions per pull request, and a real completion's 1–3s latency makes p99 ≤ 500 ms
   unreachable for reasons that say nothing about how `taxcalc-api` scales. `SyntheticChatUpstream`
   enters through the same `ChatUpstream` seam, so the price book, the `BigDecimal` arithmetic, the
   EMF log and the header are all production code — only the token counts are synthetic. The cost
   figure above comes from 10,609 real header reads.

`cost_samples: ['count>0']` is the gate on the gate: without it, a deployment that stopped emitting
the header makes every sample `parseFloat(undefined || '0')` = 0, and `0 < 0.003` passes forever
while being reported as a cost control.

The workload mix weights sum to 1.0 and are **asserted, not renormalised**. The LLM slice's 0.05 is
a ceiling forced by `RateLimitFilter`'s 10 requests/minute per JWT subject, not a preference: each
VU issues ~120 requests/minute, so 0.05 is ~6/min per subject. At the obvious-looking 0.2 every VU
would collect 429s and `http_req_failed` would blow through 0.005 — reading as "the service fell
over under load" when it is the cost control working exactly as designed.

### One image, two run modes

The worker crash-looped on `required a bean of type 'JwtDecoder' that could not be found`. Boot's
`OAuth2ResourceServerAutoConfiguration` is `@ConditionalOnWebApplication(SERVLET)` and correctly
supplies nothing to a `web-application-type=none` process; `@EnableWebSecurity` carries no such
condition, imports `WebSecurityConfiguration` anyway, and that demands the filter chain which needs
the decoder. The error names `JwtDecoder`, never the run mode, and the api pods running the
*identical image* are healthy at the time — so the natural first conclusion is a missing
environment value rather than that a worker should not be building an HTTP filter chain at all.

`TaxcalcWorker` refuses to start if its read-model listener is disabled, because the inverse
mistake — one copy-pasted env block — produces a pod that joins the group, gets partitions, reports
`Ready` and never commits an offset. Lag never falls, KEDA scales to `maxReplicaCount` and holds
there, and every surface looks correct: the ScaledObject is `READY=True` and `ACTIVE=True`, the
pods are `Running`, the generated HPA is at its ceiling "because there is work". The only symptom
is a stale read model and a bill for twenty idle pods.

### Authored and defended, never applied

Four AWS-native files under `k8s/aws-authored/` in the config repo, each opening with why it cannot run
on k3d: a Karpenter `NodePool`, an `AWS::XRay::SamplingRule`, an ADOT collector dual-exporting to
Tempo and X-Ray, and the SQS form of the KEDA trigger. The three decisions worth defending:

- **Karpenter's `limits` exist because of KEDA and the HPA.** A runaway asks for forty pods and
  Karpenter will launch whatever that needs, because unbounded provisioning is its job. `limits` is
  the only thing in the chain that says no. Stated rather than hidden: one NodePool spanning Spot
  and On-Demand does **not** keep the api off Spot — a Spot reclaim is an *involuntary* disruption
  and no PDB applies to it.
- **`FixedRate: 0` is the X-Ray trap and it fails only when it matters.** The reservoir is an
  absolute floor of 10 traces/second; `FixedRate` samples 5% above it. Zeroing the rate looks
  disciplined and holds up under load, then fails in the quiet window — which is exactly when an
  error spike is most diagnosable.
- **`identityOwner: operator`.** With `workload`, `sqs:GetQueueAttributes` lands on the *worker
  pod's* IRSA role: a permission the application never uses, carried by every replica, inherited by
  anyone who reaches any worker pod. A credential belongs to the thing that makes the call, not the
  thing the call is about.

### Honest gaps

**Argo CD now runs on this cluster, these objects were synced through it, and the first sync
failed** — the AppProject's `namespaceResourceWhitelist` was missing `batch/Job` and
`keda.sh/ScaledObject`, the two kinds **Task 1 itself added**. That is the mistake the whitelist's
own comment warns about, and it survived because `kubectl apply` consults no AppProject. Worse, a
denied resource fails the sync *operation*: the HPA and PDB were legal and unchanged and neither
converged either, because one missing line stopped every resource in the Application. Fixed, and
both now report `Synced / Healthy`. What is *still* unproven is the GitHub half — `argocd-repo-server`
cannot reach github.com through this network's TLS interception and the push guard blocks pushing
the branch, so the sync was driven from a bare mirror served by a `git daemon` pod in-cluster;
every guardrail, wave and policy exercised was the committed configuration, only the transport was
local. `manifests/` in this repo was
deliberately **not** extended: GITOPS.md already records that the migration direction is to delete
it in favour of the config repo's `k8s/taxcalc-api/`, and adding today's objects to a copy on its way out
would deepen a documented drift. And **107 of 10,716 LLM requests (1%) hit the rate limiter** during
the 12-minute run — `Math.random()` clusters, so a VU that draws the LLM branch several times in
quick succession exceeds 10/min; a burst-tolerant weight would be ~0.03.

## Week 7 Day 1 — Python Sidecar: uv, Pydantic v2, httpx & a Strict CI Gate

The first Python in this repo. `taxcalc-api` keeps the transactional Postgres workload and the
latency-sensitive HTTP surface — that is the JVM's sweet spot and where it stays. Alongside it
now sits [`taxcalc-ai/`](taxcalc-ai/), a uv-managed Python package that owns the AI/ML half of
the stack: it calls the W3 D1 LLM proxy and returns a typed result. Full write-up in
[`taxcalc-ai/PYTHON.md`](taxcalc-ai/PYTHON.md); the AI-authoring record is in
[`taxcalc-ai/PROMPT_JOURNAL.md`](taxcalc-ai/PROMPT_JOURNAL.md).

**Same repo, not a new one.** The two services share the `Taxpayer` JSON contract. A contract
change that spans two languages should be one diff, not two PRs in two repos that can drift
apart between merges.

- **uv project, `src/` layout, committed lockfile** — [`taxcalc-ai/pyproject.toml`](taxcalc-ai/pyproject.toml).
  Runtime deps are separated from dev tooling via `[dependency-groups]`, and CI runs
  `uv sync --frozen`, so `uv.lock` is the source of truth and lockfile drift fails the build
  rather than silently resolving a different dependency set than anyone tested. The `src/`
  layout is the load-bearing choice: a flat layout lets tests import the package straight out of
  the working directory, so a packaging mistake passes locally and fails only after install.
- **Pydantic v2 boundary models** — [`taxcalc-ai/src/taxcalc_ai/models.py`](taxcalc-ai/src/taxcalc_ai/models.py).
  `Taxpayer` mirrors the Java `TaxpayerReadModel` with camelCase aliases plus
  `populate_by_name=True`. Every model is `extra="forbid"` and `frozen=True`; every collection
  field is a `tuple`, never a `list`, because `frozen=True` on a model holding a list is only
  shallow. Money is `Decimal` with `max_digits=14, decimal_places=2` — the same contract as
  `BigDecimal.setScale(2, HALF_UP)` on the Java side.
- **The Java/Python round-trip — and the wire-format change it forced.** The fixture at
  `taxcalc-ai/tests/fixtures/taxpayer_java.json` is a real captured `GET
  /api/v1/taxpayers/taxpayer-001` response, not a document hand-written to match what Python
  happens to emit — which is why it exposed a genuine seam instead of hiding one. Jackson's
  default for `BigDecimal` is a bare JSON **number**; Pydantic emits a `Decimal` as a JSON
  **string**; and a JSON number's trailing zeros survive *no* parser, so `120000.00` arrives as
  `Decimal('120000')`, scale gone. No setting on either side reconciles that.
  **So the seam was removed rather than worked around:** `TaxpayerReadModel`'s money fields now
  carry `@JsonFormat(shape = STRING)`, both ends write the digits verbatim, and the round-trip
  test asserts whole-document equality with no normalisation at all
  (`json.loads(ours) == json.loads(fixture)`). This was never only about the Python test —
  JavaScript's single IEEE-754 numeric type meant the React client was parsing money into a
  float too, so `useGetTaxLiabilityRest.ts` and the server-side Zod mirror in
  `server/api/chat-tools.ts` moved to `string` in the same change. The 2-decimal scale that
  `setScale(2, HALF_UP)` computes with now survives all the way to the browser.
- **httpx client with a retry policy that actually distinguishes 4xx from 5xx** —
  [`taxcalc-ai/src/taxcalc_ai/client.py`](taxcalc-ai/src/taxcalc_ai/client.py). Retrying on
  `retry_if_exception_type(httpx.HTTPStatusError)` retries a 400 three times, which spends the
  rate-limit budget to collect the same rejection and can lock an account out on a 401. A custom
  predicate retries only timeouts, network errors and 5xx, and two tests pin the attempt counts
  (exactly 3 on a 503, exactly 1 on a 400). The retry budget is built from settings rather than
  frozen into a decorator at import time, so `proxy_max_retries` is a knob that does something.
- **Correlation-id propagation, asserted rather than assumed.** The request carries
  `x-correlation-id`; `CorrelationIdFilter` on the Java side echoes it on every response, so the
  sidecar refuses a reply whose echoed id does not match what it sent. A mismatch means the
  answer in hand belongs to a different request, and attributing it to this taxpayer would be
  worse than failing.
- **`SecretStr` for the proxy API key** — [`taxcalc-ai/src/taxcalc_ai/settings.py`](taxcalc-ai/src/taxcalc_ai/settings.py).
  It renders as `**********` in `repr()`, `str()` and `model_dump()`, so the key survives a naive
  `LOG.info("settings=%s", settings)` and a traceback that prints locals.
  `.get_secret_value()` is called in exactly one place in the package — the line that builds the
  `authorization` header — and a test asserts the key reaches no rendered log line.
- **Identifiers are never taken back from the model's own JSON.** `EstimateCompletion` carries a
  label, a confidence and a rationale, and `extra="forbid"` rejects a `taxpayerId` if the model
  volunteers one. An LLM is a plausible source of a judgement and a terrible source of an
  identity: a hallucinated id addresses the wrong taxpayer's record.
- **A strict CI gate** — [`.github/workflows/python-ci.yml`](.github/workflows/python-ci.yml).
  `uv sync --frozen` → `ruff check` → `ruff format --check` → `mypy --strict src/ tests/` →
  `pytest --cov-fail-under=85`, each as its own step so a failure names the offending step in the
  GitHub UI. `mypy` runs with `disallow_any_explicit = true` on top of `--strict`, because
  `--strict` alone still lets `Any` back in by hand.
- **Why this workflow is path-filtered and `ci.yml` is not.** Unlike the Java gate — which runs
  its full Testcontainers suite even on docs-only PRs, because a path-filtered *required* check
  leaves GitHub waiting forever for a context that never reports (it stranded Dependabot PRs
  #39–#42) — `python-ci` is not a required check, so scoping it to `taxcalc-ai/**` is safe and
  buys back exactly the thing the Java gate had to give up. The workflow header says so in place,
  so the filter comes off first if it is ever promoted to required.

**Result:** 40 tests green, 98.71% coverage, zero `mypy --strict` errors, zero `ruff` findings.


## Week 7 Day 2 — Data Tooling & AI Observability: pandas, pgvector, LangSmith, RAGAS & Great Expectations

The W7 D1 sidecar gains the data spine the rest of Week 7 reads from. Five artefacts land
together — a pandas corpus loader, an extended pgvector schema with an HNSW index, an idempotent
psycopg v3 loader, `@traceable` retrieval streaming to LangSmith, and a RAGAS + Great
Expectations gate over a real Testcontainers Postgres. Full write-up in
[`taxcalc-ai/PYTHON.md`](taxcalc-ai/PYTHON.md); the three authoring transcripts are in
[`taxcalc-ai/PROMPT_JOURNAL.md`](taxcalc-ai/PROMPT_JOURNAL.md).

**Five checkboxes, one contract.** The embedding dtype, the schema column set, the trace
decorator, the eval baseline and the data validation are a single composite thing. Any one of
them missing turns a green build into a silent retrieval-quality regression or a leaked key —
which is why they shipped in one PR rather than as five independent ticks.

- **Pandas corpus loader + `float32` discipline** —
  [`taxcalc-ai/src/taxcalc_ai/corpus.py`](taxcalc-ai/src/taxcalc_ai/corpus.py). De-duplicates on
  `(doc_id, chunk_idx)` *before* embedding — that is the key the table's `UNIQUE` constraint and
  the loader's `ON CONFLICT` both resolve on, so letting a duplicate through means paying to
  embed a chunk twice to reach the same final state. Length bounds (1–8000 chars) filter rather
  than raise, because a corpus is a bulk input and one malformed row must not fail a 100k-row
  load; the ceiling is a deliberate over-estimate of MiniLM's 256-token window, past which
  `encode` truncates *silently* and the vector describes only the first paragraph. Encoding is
  one batched call over the whole column, not `df.apply` per row.

  `.astype(np.float32)` is applied once, at the boundary, and `CorpusRow` declares
  `NDArray[np.float32]`. pgvector stores 4-byte `real` components: a `float64` array is either
  rejected or **silently narrowed** on write, and the silent case is the one that hurts — the
  insert reports success and retrieval quality degrades with nothing in the logs. The real model
  already returns `float32` on this hardware, so the test that proves the narrowing uses a stub
  that returns `float64` on purpose.

- **Extended pgvector schema** —
  [`taxcalc-ai/sql/V001__doc_chunks.sql`](taxcalc-ai/sql/V001__doc_chunks.sql). `doc_chunks` with
  `vector(384)`, a `model_version` column, `UNIQUE (doc_id, chunk_idx, model_version)`, a
  compound `(tenant_id, model_version)` b-tree, and an HNSW index using `vector_cosine_ops`
  (`m = 16`, `ef_construction = 64`). The op-class must match the `<=>` query operator: a
  mismatch does not fail and does not warn, the planner simply stops using the index and scans
  every row, and the symptom surfaces months later as "search got slow as the corpus grew".

  **Not a Flyway migration, and deliberately not under `src/main/resources/db/migration/`.**
  Flyway keys applied migrations by version and validates checksums across its whole history, so
  a sidecar-owned file in the Java service's migration path would let this Python project's
  schema changes fail the *Java* service's context startup. `V001` is the sidecar's own ordering
  from its own beginning — the same reasoning that made the W6 D4 embeddings migration `V5` and
  not the `V3` its task text named.

- **Idempotent psycopg v3 loader** —
  [`taxcalc-ai/src/taxcalc_ai/pgvector_loader.py`](taxcalc-ai/src/taxcalc_ai/pgvector_loader.py).
  `register_vector(conn)` is the first statement inside every connection — psycopg does not know
  what a `vector` is, and without the adapter the array arrives as bytes the column either
  rejects or **accepts as malformed**, producing rows that exist, look ordinary in `SELECT`, and
  rank meaninglessly. `cur.executemany` over a list of tuples pipelines the batch;
  `ON CONFLICT (doc_id, chunk_idx, model_version) DO UPDATE` makes the recovery procedure for a
  half-finished bulk load "run it again" rather than "truncate and lose the work that
  succeeded". `DO UPDATE` refreshes text and embedding but deliberately not `created_at`: a
  retry is not a new arrival.

- **`@traceable` retrieval** —
  [`taxcalc-ai/src/taxcalc_ai/rag.py`](taxcalc-ai/src/taxcalc_ai/rag.py).
  `@traceable(run_type="retriever", name="taxcalc_ai.retrieve_chunks")` wraps the whole function
  including the embedding step, so "was it the encode or the index" is answerable from one span.
  Both `WHERE` filters are applied before ranking and they are different kinds of filter:
  `tenant_id` is the security boundary (an HNSW index is an ANN structure over the vector column
  alone and cannot enforce it) and `model_version` is the correctness boundary (two models'
  vectors share a 384-dimensional space without meaning the same thing). Two tests pin each,
  with the other tenant's rows placed deliberately *nearer* the query so a dropped filter fails
  rather than passing by luck. The credential check runs at import, not on first call.

- **A SaaS-side check that tracing actually works** —
  [`taxcalc-ai/src/taxcalc_ai/scripts/assert_langsmith_run_visible.py`](taxcalc-ai/src/taxcalc_ai/scripts/assert_langsmith_run_visible.py).
  A grep for `@traceable` proves the decorator is written in the file. It does not prove a single
  trace ever left the process — and every realistic failure leaves the decorator exactly where it
  was: `LANGSMITH_TRACING` unset makes `@traceable` a documented no-op, a key scoped to the wrong
  workspace uploads to a project nobody reads, a misspelt project name is created on demand and
  swallows the runs, and a process that exits before the background uploader flushes traces
  nothing at all while long-running services trace fine. Each is a green build and a silent
  observability gap. The script fires a real retrieval through the real decorated function,
  flushes, then polls LangSmith over a bounded lookback so a leftover run from a previous job
  cannot make a broken build pass. It provisions its own corpus — with `TAXCALC_AI_PG_DSN`
  unset it starts a throwaway pgvector container, applies the DDL and embeds the seed set — so
  the documented one-line invocation works on a laptop and the CI step is the same command
  rather than thirty lines of YAML that nothing could lint or test. A LangSmith that cannot be
  reached is reported as its own verdict: both exit non-zero, but "could not query" and "no run
  visible" send the reader to different places and must not read alike.

- **RAGAS 50-row golden baseline** —
  [`taxcalc-ai/tests/golden/taxcalc_golden_50.jsonl`](taxcalc-ai/tests/golden/taxcalc_golden_50.jsonl)
  and [`taxcalc-ai/tests/test_ragas_thresholds.py`](taxcalc-ai/tests/test_ragas_thresholds.py).
  30 clean rows plus 20 reproducing three failure modes — missing context, junk context,
  near-duplicate context. That ratio is the point: a golden set scoring 1.0 everywhere has no
  headroom to fall and therefore cannot detect a regression. A credential-free test asserts the
  mix is still present, so a regenerated all-clean set fails loudly instead of quietly raising
  every metric and making the build *greener* than before. The four floors are currently
  **declared, not measured**: the evaluator workspace is spend-capped, so the threshold test
  skips and says so in those words. It reads its credential from `ANTHROPIC_API_KEY` or
  `TAXCALC_AI_ANTHROPIC_API_KEY`, in the environment or in the gitignored `.env`.

  The evaluator LLM and embeddings are passed **explicitly**. `evaluate(dataset, metrics=[...])`
  with nothing else lets RAGAS build its own defaults, and those defaults are OpenAI — so a CI
  job supplying only `ANTHROPIC_API_KEY` does not evaluate against Claude, it fails on OpenAI
  auth, or silently bills a different provider if an `OPENAI_API_KEY` happens to be present.

- **Great Expectations over a real Postgres** —
  [`taxcalc-ai/tests/test_great_expectations_suite.py`](taxcalc-ai/tests/test_great_expectations_suite.py).
  The `doc_chunks_v1` suite runs against a Testcontainers `pgvector/pgvector:pg16`, not a Pandas
  frame. A validation against the frame the loader was handed proves the loader *received* good
  data; it says nothing about what arrived — and the failures worth catching (a `vector` column
  that took malformed bytes, a relaxed `NOT NULL`, an `ON CONFLICT` that quietly halved the row
  count) all live on the far side of the insert. A negative-control test asserts an impossible
  row count and requires `success is False`, because `assert result.success is True` alone is
  indistinguishable from a checkpoint that reports success wherever it is pointed.

- **CI gate: three new steps** —
  [`.github/workflows/python-ci.yml`](.github/workflows/python-ci.yml). GX checkpoint, RAGAS
  thresholds, LangSmith run-visibility. All three run in the existing job, reusing the uv
  environment rather than re-resolving and re-downloading the ~80 MB model in a fresh one. Every
  credential arrives from `secrets.TAXCALC_AI_*`; none appears in the tree. The CI LangSmith
  project is `taxcalc-ai-dev-ci`, **not** the dev project, so gate-run traces do not pollute the
  view an engineer reads while debugging. The coverage step deselects both the slow RAGAS test
  and the GX suite so a failure names one of three distinct problems rather than a vague one.

**Two findings worth carrying forward.**

The Great Expectations suite failed on perfectly valid data until pgvector's SQLAlchemy type was
registered. GX resolves a `table.column_types` metric before evaluating *any* column-level
expectation, by reflecting the table and compiling each column's type to a string. SQLAlchemy
core has never heard of `vector`, so `embedding` reflects as `NullType()` and compiling it raises
`CompileError`. GX catches that per-expectation and reports `"success": false` with an **empty**
result dict — so the symptom is four column expectations failing while the row-count expectation
passes with `observed_value: 100`, which reads unmistakably like a data problem. The fix is one
import for its side effect (`import pgvector.sqlalchemy`, which registers `VECTOR` in the
dialect's `ischema_names`). Nothing in the GX report mentions a type.

`UNIQUE (doc_id, chunk_idx, model_version)` does **not** include `tenant_id`, so `tenant_id` is
not part of the `ON CONFLICT` arbiter either. Left alone, a second tenant loading a chunk whose
`doc_id` another tenant owns would take the `DO UPDATE` branch: its content would overwrite the
incumbent's while `tenant_id` stayed put, and the tenant-scoped read path would then serve one
tenant's text to another. An `INSERT` that reports success and leaks across a tenant boundary is
the worst shape a defect can take here. Found the hard way, when three loader tests failed
against a loader behaving exactly as designed.

**The schema is unchanged and the loader closes it.** The `ON CONFLICT` clause carries
`WHERE doc_chunks.tenant_id = EXCLUDED.tenant_id`, so a cross-tenant collision matches nothing,
and `load_rows` turns the `cur.rowcount` shortfall into `CrossTenantDocIdError` *before* the
commit — the batch rolls back whole. A quiet read-time leak became a loud write-time failure at
the cost of no extra round trip. Three tests pin it: the raise, the whole-batch rollback, and
that a same-tenant reload is still idempotent, since a guard that also blocked legitimate retries
would have removed the property the loader exists to provide.

**Result:** 69 tests green, 89.65% coverage against an 85% floor, zero `mypy --strict` errors,
zero `ruff` findings. Both repository secrets are set.

**The LangSmith run-visibility gate is verified end to end** — a real retrieval, flushed, then
found SaaS-side in `taxcalc-ai-dev-ci` (`exit 0`). The first poll came back empty and the second
succeeded, which is the background-flush window the script's retry loop exists for, observed
rather than assumed.

**The RAGAS floors are still unobserved, and the skip says so.** The Anthropic key is valid
(`models.list` succeeds) but the workspace is spend-capped until 2026-10-01, so the evaluation
judged nothing. Raising the workspace spend limit in the Anthropic console and re-running is
what turns the floors from declared into recorded — the skip message states in those words that
the run evaluated nothing, so a green CI cannot be mistaken for a measured baseline.

Getting there produced two changes worth naming. RAGAS's executor catches each job's exception
itself and writes `NaN` into that row's score, so a dead evaluator does not raise — it returns a
full result of `NaN`, and `assert nan >= 0.80` reads in CI as a quality regression. The gate now
detects the NaN: *all* metrics NaN is a provisioning fact and skips, *some* metrics NaN still
fails. And `RunConfig(max_retries=3, …)` replaced RAGAS's default of ten-with-backoff, which had
each of ~200 jobs exhausting its retries — 13m40s to report something knowable in seconds, inside
a 30-minute CI budget. It now settles in ~33s.


## Week 7 Day 3 — RAG 2.0 Production Retrieval: hybrid + RRF + MMR + bge-reranker + semantic cache

The W7 D2 sidecar's retrieval was one cosine ANN query. Today it becomes a production retrieval
pipeline: hybrid dense + sparse search fused by Reciprocal Rank Fusion, MMR diversification, a
cross-encoder reranker under a strict 300 ms soft deadline, tenant + JSONB metadata
pre-filtering against per-tenant partial HNSW indexes, a Redis semantic cache, an Airflow
ingest DAG, and a `faithfulness >= 0.85` CI gate. Full write-up in
[`taxcalc-ai/PYTHON.md`](taxcalc-ai/PYTHON.md); the authoring transcripts, including the one
where Claude invented an entire measurement matrix, are in
[`taxcalc-ai/PROMPT_JOURNAL.md`](taxcalc-ai/PROMPT_JOURNAL.md).

**Six knobs, one contract.** The schema, the fusion algorithm, the reranker timeout, the cache
key shape, the tenant pre-filter and the faithfulness gate are a single composite thing. Any one
of them missing turns a green build into a silent retrieval-quality regression, a tenant leak,
or a p99 violation — six checkboxes ticked independently is exactly the failure mode.

| Artefact | What it is | Why it is shaped that way |
|---|---|---|
| [`sql/V002__rag2_metadata_and_partial_indexes.sql`](taxcalc-ai/sql/V002__rag2_metadata_and_partial_indexes.sql) | `chunk_metadata jsonb` + `content_hash` + GIN `jsonb_path_ops` + three per-tenant partial HNSW (`m=24`, `ef_construction=128`) + generated `chunk_tsv` | HNSW cannot see `tenant_id`, so a global index collects candidates from every tenant and discards them after ranking — under-recalled, silently, with no plan change. Every index is `CREATE INDEX CONCURRENTLY`. |
| [`chunker.py`](taxcalc-ai/src/taxcalc_ai/chunker.py) | `RecursiveCharacterTextSplitter`, 900/150, coarse-to-fine separator ladder | `overlap >= chunk_size/2` is rejected: at `overlap == chunk_size` the stride is zero and the splitter cannot terminate. Chunk ids are per-document so one document gaining a paragraph cannot renumber another's citations. |
| [`embedder.py`](taxcalc-ai/src/taxcalc_ai/embedder.py) | The pre-embed gate | `ON CONFLICT DO UPDATE` made the write idempotent; it did not make it cheap, because the embedding it overwrote had to be computed first. One SELECT on `(content_hash, model_version)` turns a re-ingest of an unchanged corpus into a round trip. |
| [`hybrid.py`](taxcalc-ai/src/taxcalc_ai/hybrid.py) | Dense ANN + Postgres FTS, fused by RRF at `k=60`, plus a coverage diagnostic | **Fusion is on rank, not score.** Cosine distance is bounded and smaller-is-better; `ts_rank_cd` is unbounded and larger-is-better. Any fixed blend weights whichever scale is larger *on this query*. There is deliberately no rescaling helper, and the gate greps for its absence. |
| [`rerank.py`](taxcalc-ai/src/taxcalc_ai/rerank.py) | MMR at `lambda=0.7` (60 → 20), then `BAAI/bge-reranker-base` (20 → 6) under 300 ms | MMR first, because the cross-encoder costs a forward pass per candidate and RRF's head can be five restatements of one fact. The timeout **fails soft** and reports `rerank_timed_out` to the caller and to the LangSmith span. |
| [`cache.py`](taxcalc-ai/src/taxcalc_ai/cache.py) | Redis cache keyed `(tenant_id, epoch, quantised embedding)` | A cache keyed on the embedding alone is a cross-tenant leak that *improves* the metric it would be noticed by. `tenant_id` is a key component **and** every citation is re-checked on every hit. `bump_epoch` invalidates a tenant in one `INCR`. |
| [`dags/rag_svc_ingest.py`](taxcalc-ai/src/taxcalc_ai/dags/rag_svc_ingest.py) | TaskFlow DAG: `load_docs` → `chunk_docs` → `embed_chunks` → `upsert_chunks` → `bump_cache_epochs` | The bump is last and only on success. Run first, a failed upsert leaves the cache emptied and the corpus unchanged — every later question pays full price to rebuild answers identical to the ones just discarded. |
| [`rag.py`](taxcalc-ai/src/taxcalc_ai/rag.py) | `retrieve_and_generate` — the entry point W7 D4's MCP server publishes and W7 D5's LangGraph nodes call | Signature pinned today (keyword-only after `tenant_id`) so both later days are wiring, not re-negotiation. Four `RAG_USE_*` flags, all defaulting to **on**, stay live two weeks for an A/B rollback to the W7 D2 baseline. |
| [`eval/run_ragas.py`](taxcalc-ai/src/taxcalc_ai/eval/run_ragas.py) | Six-column before-vs-after harness | A matrix, not a before/after pair: all-four-on tells you the bundle helped, not that all four helped. One stage may be neutral and one may be negative and masked. |

**`retrieve_chunks` was not replaced.** It is the W7 D2 baseline, and three things are defined in
terms of it: the LangSmith visibility gate, the report's baseline column, and the A/B rollback
target. `retrieve_and_generate` was added beside it.

**"Nothing was judged" is not a diagnosis.** The report, `PYTHON.md` and the PR description all
said "spend-capped" for most of this branch's life, on no evidence: the annotation CI emits comes
from the all-NaN branch, which fires for *any* per-job failure — revoked key, wrong model id,
blocked egress, rate limit — because RAGAS catches each job's exception itself, logs it, and
writes NaN. Four different fixes behind one identical green run. The gate now captures those log
records and names the cause, and the first run with it returned `You have reached your specified
workspace API usage limits. You will regain access on 2026-10-01 at 00:00 UTC.` The inference was
right; it took a code change to *know* it — and it surfaced a fact nobody had, that the limit is
**periodic**, so the gate measures itself for free on 1 October with no configuration change.

**The RAGAS gate skipped, and the report says so.** `faithfulness >= 0.85` raises `SystemExit`;
the other three metrics are asserted floors that diagnose the cause rather than being the
user-facing failure. No evaluator credential exists in this environment, so the gate **skips** —
and [`taxcalc-ai/docs/ragas/w7d3.md`](taxcalc-ai/docs/ragas/w7d3.md) carries `n/m` in every cell
under a "Status of this report: NOT MEASURED" heading rather than plausible invented numbers.
`conftest.py` re-emits the skip as a GitHub Actions annotation and a job-summary line, and a
credential-free test asserts the 0.85 gate is strictly above the W7 D2 floor so it cannot be
quietly lowered while it cannot be measured. Claude's first draft of that report contained a
complete matrix and an attribution paragraph reasoning from it; see `PROMPT_JOURNAL.md` Q-section
for why that was rejected rather than trimmed.

**The reranker timeout measured the model load.** `_get_reranker()` is lazy, so the clock started
before ~1.1 GB of weights were constructed — the first rerank of every process breached 300 ms
and fell back to retrieval order. A cold-start artefact reported as a quality event, which would
spike the `rerank_timeout` metric on every deploy and page somebody for a healthy system. Caught
by a test, not by review.

**CI gains four steps**, ordered cheapest-first in the existing job: the Airflow DAG import
check (a second, no container), tenant + metadata isolation, the semantic-cache smoke test, and
the RAGAS faithfulness gate last because it is the only step that spends money.

**Result:** 122 tests green in the fast gate (88.12% coverage against an 85% floor), plus 2
tenant-isolation, 4 semantic-cache and 2 Great Expectations tests in their own steps; zero
`mypy --strict` errors across `src/` and `tests/`; zero `ruff` findings; all four gate greps
empty.


## Week 7 Day 4 — Publishing the Capstone as an MCP Server: FastMCP, stdio + HTTP/SSE, four tools

W3 D1 built REST services. W7 D3 built a retrieval pipeline. Today all of it lands behind **one
MCP surface** that Claude Desktop and the W7 D5 multi-agent orchestrator both speak to: a new
sibling project [`taxcalc-mcp-server/`](taxcalc-mcp-server/) that imports the sidecar as a path
dependency and publishes four tools plus one read-only resource over two transports. Authoring
transcripts in [`taxcalc-mcp-server/PROMPT_JOURNAL.md`](taxcalc-mcp-server/PROMPT_JOURNAL.md);
the consumer's-eye view of what this means for the sidecar is in
[`taxcalc-ai/PYTHON.md`](taxcalc-ai/PYTHON.md#what-w7-d4-adds).

**Seven knobs, one contract.** Transports, schemas, error codes, the idempotency key, tracing,
packaging and the CI tiers are a single composite surface that downstream LLM clients code
against. Any one of them missing turns a green build into a double-debited refund, a
stdout-corrupted stdio session, a tool description Claude silently skips, or a context-bloating
DTO — seven checkboxes ticked independently is exactly the failure mode.

| Artefact | What it is | Why it is shaped that way |
|---|---|---|
| [`app.py`](taxcalc-mcp-server/src/taxcalc_mcp_server/app.py) | FastMCP + `@asynccontextmanager` lifespan, one shared `httpx.AsyncClient`, logging pinned to stderr twice | On stdio **stdout is the protocol**; one stray byte corrupts the frame a client is mid-parse of. Both the stdlib and structlog paths are redirected, and `ruff`'s `T20` bans `print` package-wide, because pinning one of the two leaves the other free to kill the session. |
| `StructuredErrorFastMCP` + `install_structured_error_handler` | Lets a tool's numeric error code reach the client | **The defect this closes was silent.** `Tool.run` wraps every handler exception into an English `ToolError`, and the low-level handler turns whatever escapes into an `isError` result — so a 404 arrived as `'Error executing tool orders.get_order: {"error": "order not found"}'` with **no `4040` anywhere**. The whole centralised error table, discarded one layer below the code that built it. Both layers had to be opened; fixing only the first removes the prefix and changes nothing else. |
| `enforce_strict_tool_schemas` | `additionalProperties: false` **and** `extra="forbid"` on the generated argument model | FastMCP builds the top-level argument model with `extra` at `"ignore"`, so a hallucinated or typo'd argument was silently dropped — and the published schema advertised nothing, so a well-behaved client could not detect it either. Advertising a rule without enforcing it would be the worse half: it invites clients to trust a check that is not happening. |
| [`tools/orders.py`](taxcalc-mcp-server/src/taxcalc_mcp_server/tools/orders.py) | `Decimal` money, a float-rejecting validator, a 2-dp scale rule, UUID v4 idempotency key | `Decimal(0.1)` is `0.1000000000000000055511151231257827` — by the time a float reaches the model the exact value is gone, so floats are refused outright rather than coerced. `10.001` is **rejected, not rounded**: rounding would refund a different amount than the caller asked for and tell nobody. On the wire it is `str(args.amount)`, so `BigDecimal` reads the scale too. |
| The idempotency key | Travels as a JSON field **and** an `Idempotency-Key` header | The body field is what the order service persists; the header is what any proxy, retry middleware or service mesh in between reads. Body-only leaves an infrastructure retry — the kind this code never sees — free to replay the request as a second refund. The key has **no default**: a generated one would make every retry a new key, arriving at the exact double-debit it exists to prevent by being helpful. |
| [`tools/rag.py`](taxcalc-mcp-server/src/taxcalc_mcp_server/tools/rag.py) | A pre-shaped 5-field DTO over the W7 D3 pipeline, `to_thread` + `wait_for`, 5040 on a miss | The pipeline returns every citation's full `chunk_text`; passing it through restates the text the answer was just generated from, doubling the token cost of every grounded answer forever. The cross-encoder has no await point, so calling it from a coroutine stalls every other in-flight request on the process. |
| [`transports/sse.py`](taxcalc-mcp-server/src/taxcalc_mcp_server/transports/sse.py) | Raw-ASGI bearer middleware, JWKS validation **opt-in** | The bearer is captured at the `GET /sse` handshake, not at `POST /messages/`, because the **entire session runs inside the handshake's coroutine** and an asyncio task inherits the context it was created in — a `ContextVar` set during a POST is invisible to the tool call. `BaseHTTPMiddleware` runs downstream in a separate task and breaks exactly that, hence raw ASGI. Local JWKS validation defaults **off**: the Java services validate authoritatively, and a second validator on the wrong issuer is not defence in depth, it is an outage that looks like a broken service. |
| [`scripts/replay.py`](taxcalc-mcp-server/src/taxcalc_mcp_server/scripts/replay.py) | Fixture replay, per-tool p50/p95/p99, ±15% p95 gate | Times **this server's own work** against canned upstreams, deliberately excluding the network: a change here cannot make the network faster, and a gate that fires on other teams' deploys stops being read. Compares p95 as a **ratio against the previous run**, because an absolute millisecond budget is a statement about the CI runner's instance type, not about the diff. |
| [`tests/test_tool_descriptions.py`](taxcalc-mcp-server/tests/test_tool_descriptions.py) | ≥200 chars, `Use this`, `Do NOT`, a closing example, plus `mcp.json` drift | A tool that raises gets an error someone can act on. A tool whose description does not say *when* to use it simply never gets called — the model picks something else, answers worse, and **nothing logs a problem**. There is no stack trace for "the model did not consider this tool". |
| [`taxcalc-orders/`](taxcalc-orders/) | A standalone Spring Boot order service: two endpoints, Postgres, Flyway, no JPA | Built because the course's `uptimecrew/taxcalc-orders:w3d1` image is not pullable here, and an E2E that skips is an E2E that proves nothing. Standalone rather than a slice of the monolith, which needs MongoDB, Redis, Kafka and an OAuth2 issuer to reach a healthy state — a nine-container test mostly exercising infrastructure. **Idempotency is a unique index on `(tenant_id, idempotency_key)`, not application code**: two retries arrive concurrently as a matter of course, so "check the key, then insert" lets both check, both find nothing, and both insert, debiting the ledger twice while every line of code looks correct in review. |
| [`taxcalc_mcp_server-ci.yml`](.github/workflows/taxcalc_mcp_server-ci.yml) | PR tier (unit + schema + description + 100-call smoke + replay + wheel), merge tier (Testcontainers E2E) | The PR tier catches everything inside this codebase; it **cannot** catch drift between this server and the Java one, because it supplies its own stub upstream. That is what the merge tier is for. The merge tier also **fails on a skip** — the E2E skips itself when Docker or a JDK is unavailable, which is right on a laptop and wrong on `main`, where a skip would let integration drift through under a green tick. |

### Three defects found by running it, none by reading it

All three passed `ruff`, `mypy --strict` and a careful read. Each was caught by driving the real
server over a real transport:

1. **Tool error codes never reached the client** (above). The entire `_map_http` contract — the
   thing the W7 D5 agent's backoff branches on — was being discarded by the SDK.
2. **A phantom `config` parameter in every published schema.** Stacking `@mcp.tool` over
   `@traceable` makes FastMCP derive the schema from langsmith's *wrapper* signature, so each
   tool advertised an argument that does not exist and that a model could try to fill. Each tool
   is now two functions: the outer owns the protocol boundary, the inner is traced.
3. **The first SSE client paid for a tool it never called.** The lifespan imported the RAG
   pipeline, so the first connection blocked on an 80 MB model load and five model-hub retries
   before the handshake completed. The import moved to first use, on the worker thread the
   pipeline already runs on.

A fourth was found by the latency gate rather than by a test: the RAG tool reached for Postgres,
Redis and an Anthropic key itself, so a **fully stubbed** pipeline still demanded a live
database and the gate could not run anywhere but production. All of it now resolves behind
`rag_entrypoint`'s `(question, tenant_id, top_k)` signature — the injection seam.

### Honest gaps

* **The order service is ours, not the course's image.** `uptimecrew/taxcalc-orders:w3d1`
  returns `pull access denied` here, so [`taxcalc-orders/`](taxcalc-orders/) is a real
  implementation of that contract rather than the course's binary. The E2E therefore proves this
  capstone's two services agree with each other; it cannot prove agreement with an image nobody
  here can run. Everything it asserts — cross-language field names, `BigDecimal` scale, the
  `Idempotency-Key` header, a single ledger row — is real, and the service is deployable.
* **The order service authenticates by presence, not by signature.** `TenantAuthFilter` requires
  a bearer token and an `X-Tenant` header; it does not verify the signature, issuer, audience or
  scopes, because doing so needs an identity provider in the test topology. Presence is exactly
  the property the E2E asserts and exactly the one that regresses — a refactor that drops the
  `Authorization` header is caught. **It is not a trust boundary and must not be deployed as
  one**; the service that owns the data verifies the token against the real issuer.
* **`llm.chat` speaks both proxy dialects, selected by the configured path.** The Java
  `LlmProxyController` serves `POST /v1/completions` taking `{prompt, model, feature}`, while the
  generic shape the brief names is `/v1/chat/completions` taking a `messages` array. Both are
  implemented: `_wire_shape` reads `llm_proxy_chat_path`, and a path ending `/chat/completions`
  gets a real `messages` array plus `max_tokens` and parses `choices[0].message.content`, while
  anything else gets the Java record's three fields and parses `text`/`inputTokens`. The MCP-facing
  schema is `messages`/`max_tokens` either way — that is the contract, and the wire format below it
  is a deployment detail. The dialect is derived from the path rather than carried in a second
  setting, because the failure mode of the two disagreeing is a **200 with an empty completion**:
  billed, logged as success, and wrong. `tests/test_llm_wire_shapes.py` drives both through real
  MCP dispatch and asserts the two upstreams parse into an identical DTO. The default stays
  `/v1/completions` because that is the route that exists in this repo; defaulting to the generic
  path would ship a server whose one LLM tool 404s out of the box.
* **The name of the inexact binary type lives in one module, and it is not under `tools/`.**
  `numeric.py` is where this package's three kinds of number are told apart: money is `Decimal`
  and never a binary fraction (`is_inexact_binary`), a retrieval score is a `RelevanceScore` and
  correctly *is* one, and a token count or a cost in minor units is an `int` because it gets
  summed. The W7 D4 money gate greps `tools/` for that type's name and expects nothing, which now
  holds literally — a tool validates, forwards and re-shapes, and "is this type acceptable for
  this quantity" is answered once, centrally, for all four tools. The alternative was deleting the
  docstrings that explain the rule in order to turn a grep green, and a check whose green state
  costs you the reasoning is a check that teaches people to delete reasoning.
* **`mcp` is pinned `>=1.2,<2`.** mcp 2.x renames `FastMCP` to `MCPServer` and changes the
  decorator and lifespan surfaces. Everything downstream — the committed `mcp.json`, the Claude
  Desktop launcher, the W7 D5 agent — is written against the v1 contract, so the pin is what
  keeps that contract true. Moving to 2.x is a rewrite of `app.py` and both transports.

**Result:** 73 Python tests green (75.38% coverage against a 70% floor), **6 Testcontainers E2E
tests green** against Postgres + the real Spring service, and 13 JUnit 5 tests on the order
service; zero `mypy --strict` errors across `src/` and `tests/`; zero `ruff` findings; the wheel
builds and exposes both console scripts; all four tools replay with p95 under 1 ms except the
first-call outlier on `orders.create_refund`.


## Build and Test

```bash
./gradlew build   # compile and run all checks (the Spring Boot service)
./gradlew test    # run the JUnit 5 test suite
./gradlew integrationTest --tests '*TaxpayerEmbeddingsRepoTest'   # container-backed tests only
```

```bash
# The self-hosted embeddings service on a local k3d cluster, and the Task 3 Done-When check.
# Idempotent; ~5 min cold, almost all of it the 1.3GB model download.
scripts/embeddings-up.sh --smoke
kubectl -n taxcalc-dev get deploy embeddings      # -> 1/1 Available
./gradlew test --tests '*EmbeddingsClientLiveIT'  # green while the script's port-forward is up
```

```bash
# The two psql-level pgvector checks, from a real psql session against a throwaway container:
# the `vector` extension is installed, and EXPLAIN ANALYZE on the nearest-neighbour query picks
# `Index Scan using taxpayer_embeddings_hnsw` with EVERY PLANNER SETTING LEFT ALONE.
#
# TaxpayerEmbeddingsRepoTest asserts the same index fact with enable_seqscan/enable_sort off,
# which isolates "can this index serve a cosine query at all" - the question an operator-class
# mismatch silently answers no to - but deliberately does not show the planner CHOOSING it.
# This script pays that other half; it is not in CI because the seed is slow and the row count
# that makes the planner's choice meaningful moves between Postgres versions.
scripts/verify-pgvector.sh              # 5000 rows, ~30s
ROWS=50000 scripts/verify-pgvector.sh   # more rows, slower seed
```

```bash
# The W5 D4 Lambda is a separate Maven build over com.uptimecrew.tax_liability.lambda only.
mvn -B -ntp test                          # JUnit 5 + Mockito + AssertJ, no AWS needed
sam validate --lint --region us-east-1    # cfn-lint over the transformed template
sam build --use-container                 # build inside the AWS Lambda java21 parity image
sam local invoke TaxpayerLookupFunction --event events/get-taxpayer.json
```

```bash
# W6 D5 - the two autoscalers, the load-test gate, and the integration spike.
# All local: k3d + KEDA + the Prometheus Adapter. No cloud account, no spend.

# Prerequisites, once per cluster (KEDA is free and installs in seconds; you own the cluster,
# so you install it - this is a real deliverable step, not an assumption):
helm repo add kedacore https://kedacore.github.io/charts && helm repo update
helm install keda kedacore/keda --namespace keda --create-namespace --set image.pullPolicy=IfNotPresent
helm install prom-adapter prometheus-community/prometheus-adapter --namespace monitoring \
  -f ../arush-adabala-tax-liability-config/k8s/taxcalc-api/prometheus-adapter-values.yaml

# Mint the load-test JWTs and publish the public key into the cluster. The private half stays
# in ./.loadtest (gitignored) and never leaves this machine.
scripts/loadtest-token.sh

# Drive KEDA 0 -> N -> 0 on real consumer-group lag. Stages the backlog first - see the script
# for why producing without pausing measures nothing.
scripts/w6d5-spike.sh
kubectl -n taxcalc-dev get scaledobject taxcalc-worker-scaledobject -w
kubectl -n taxcalc-dev get deploy taxcalc-api-worker -w

# The SLO gate. Thresholds are pinned to the W5 D5 SLO; the k6 exit code is the gate.
k6 run -e TARGET=http://localhost:8080 -e TOKENS="$PWD/.loadtest/tokens.json" \
  loadtests/taxcalc-api-p99.js

# Saturation probe: no think time, so in-flight concurrency equals the VU count. This is how
# the HPA's averageValue: 6 was derived, and the only way to drive the metric on demand -
# at the gate's own settings the service is too fast to saturate and the HPA correctly
# holds at minReplicas.
k6 run -e SLEEP=0 -s 30s:60 -s 150s:60 -e TARGET=... -e TOKENS=... loadtests/taxcalc-api-p99.js
kubectl -n taxcalc-dev get hpa taxcalc-api-hpa -w
```

```bash
cd taxcalc-web
pnpm install
pnpm exec playwright install chromium   # once, before the first `pnpm check` or `pnpm e2e`
pnpm check                              # tsc --noEmit && eslint . && vitest run --coverage && playwright test - same gate as .github/workflows/web-ci.yml
pnpm dev                                # http://localhost:5173/login
```
```bash
# W7 D1 - the Python sidecar. Mirrors .github/workflows/python-ci.yml step for step.
cd taxcalc-ai
uv sync                                 # creates .venv from the committed lockfile
uv run ruff check && uv run ruff format --check
uv run mypy --strict src/ tests/        # strict + disallow_any_explicit
uv run pytest -v --cov=src --cov-fail-under=85
uv run python -m taxcalc_ai.cli request.json   # validate a payload at the boundary

# W7 D2 - the data + AI-observability stack. The container-backed tests need a running
# Docker daemon; the first run also downloads the ~80MB sentence-transformers model.
uv run pytest -v tests/test_corpus.py
uv run pytest -v tests/test_pgvector_loader.py          # Testcontainers + EXPLAIN-HNSW
uv run pytest -v tests/test_rag_traceable.py
uv run pytest -v tests/test_great_expectations_suite.py # Testcontainers + GX doc_chunks_v1
# The RAGAS gate reads ANTHROPIC_API_KEY or TAXCALC_AI_ANTHROPIC_API_KEY, from the environment
# or from the gitignored .env; it skips without one, and a skip means the floors are declared,
# not measured. The LangSmith gate needs only LANGSMITH_API_KEY - it starts and seeds its own
# pgvector container when TAXCALC_AI_PG_DSN is unset.
uv run pytest -v -m slow tests/test_ragas_thresholds.py # needs an evaluator key
uv run python -m taxcalc_ai.scripts.assert_langsmith_run_visible  # needs LANGSMITH_API_KEY

# The two secret-scan greps the gate runs. Both must return nothing.
#
# The LangSmith prefix is assembled rather than written out, because a doc that spells the
# literal is itself a hit for the scan it documents - which is exactly how this line first
# failed the gate it describes.
grep -RIn "lsv2""_pt_" .
grep -RIn 'except:' src/ tests/

# W7 D3 - the RAG 2.0 production retrieval stack. The container-backed tests need a running
# Docker daemon (Postgres + pgvector AND Redis); the first rerank run downloads the ~200MB
# bge-reranker-base weights.
uv run pytest -v tests/test_chunker.py                  # chunking discipline, no container
uv run pytest -v tests/test_hybrid_rrf.py               # metadata filter, exact-phrase FTS, RRF
uv run pytest -v tests/test_rerank.py                   # MMR limits + timeout-and-fallback
uv run pytest -v tests/test_semantic_cache.py           # Testcontainers Redis
uv run pytest -v tests/test_tenant_isolation.py         # DB-side tenant assertion
uv run pytest -v tests/test_ingest_dag.py tests/test_eval_matrix.py
uv run pytest -v tests/test_rag_pipeline.py             # retrieve_and_generate end to end
# The W7 D3 CI gate: faithfulness >= 0.85 raises SystemExit. Skips without an evaluator key,
# and a skip means the gate is DECLARED, not measured - see docs/ragas/w7d3.md.
uv run pytest -v -m slow tests/test_ragas_gate.py
# Importability is the bar for the DAG, not a running scheduler.
uv run python -c "from taxcalc_ai.dags.rag_svc_ingest import taxcalc_ai_ingest_dag"
# The before-vs-after report. Needs TAXCALC_AI_PG_DSN, TAXCALC_AI_REDIS_URL and an evaluator key.
uv run python -m taxcalc_ai.eval.run_ragas --matrix

# The two W7 D3 gate greps, in addition to the two above. Both must return nothing.
grep -RIn 'CREATE INDEX [^C]' sql/V002__rag2_metadata_and_partial_indexes.sql
grep -RIn 'normalize.*score\|min[_-]max' src/taxcalc_ai/hybrid.py

# Behind a TLS-inspecting corporate proxy, uv needs the system trust store. Deliberately not
# baked into pyproject.toml or CI: GitHub runners do not need it, and a config that always
# trusts the system store is a config that hides a real certificate problem.
UV_SYSTEM_CERTS=1 uv sync
# uv's flag does NOT cover huggingface_hub, which has its own TLS stack - the bge-reranker
# download fails with CERTIFICATE_VERIFY_FAILED until the system roots reach Python directly:
{ cat "$(uv run python -c 'import certifi;print(certifi.where())')"
  security find-certificate -a -p /Library/Keychains/System.keychain
  security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain
} > /tmp/ca-bundle.pem
REQUESTS_CA_BUNDLE=/tmp/ca-bundle.pem SSL_CERT_FILE=/tmp/ca-bundle.pem uv run pytest tests/test_rerank.py

# W7 D4 - the MCP server. A separate uv project; `cd` out of taxcalc-ai first.
cd ../taxcalc-mcp-server
uv sync --frozen
uv run ruff check
uv run mypy --strict src/ tests/
# The PR tier. The E2E is excluded by MARKER, not filename, so a mis-marked test fails loudly
# on the merge tier rather than quietly never running.
TAXCALC_MCP_BEARER_JWT=dummy-for-tests LANGSMITH_API_KEY=dummy-for-tests \
  uv run pytest -v -m "not e2e" --cov=src --cov-fail-under=70
# The latency gate. Replays recorded tool calls against canned upstreams and writes
# .replay/latest.json; add --compare-to to fail on a >15% p95 regression.
TAXCALC_MCP_BEARER_JWT=dummy-for-tests LANGSMITH_API_KEY=dummy-for-tests \
  uv run python -m taxcalc_mcp_server.scripts.replay --fixtures tests/fixtures/
# The order service the E2E runs against (a standalone Gradle build; the root wrapper drives it).
cd .. && ./gradlew -p taxcalc-orders test        # 13 JUnit 5 tests, no containers
./gradlew -p taxcalc-orders bootJar              # the artefact the E2E's image copies in
cd taxcalc-mcp-server
# The merge tier: Postgres + taxcalc-orders + the MCP server, three real processes. Needs Docker
# and a JDK; it builds the jar itself if missing and SKIPS with the exact cause if it cannot,
# which CI treats as a failure.
uv run pytest -v -m e2e

# Drive the stdio server by hand, the way Claude Desktop does.
npx @modelcontextprotocol/inspector uv run python -m taxcalc_mcp_server.transports.stdio
# Or serve HTTP+SSE for the W7 D5 agent. An unauthenticated probe is REFUSED - that 401 is the
# proof the bearer middleware is in front of the transport, and the healthcheck treats it as
# healthy for exactly that reason.
TAXCALC_MCP_PORT=8080 uv run taxcalc-mcp-server-sse &
curl -s http://127.0.0.1:8080/sse                                  # -> {"code":4030,...}, HTTP 401
curl -sN -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/sse   # -> event: endpoint

# Package it. A packaging mistake breaks the Claude Desktop integration while every test passes.
uv build && unzip -p dist/*.whl '*/entry_points.txt'
# `pipx install ./dist/*.whl` ALONE FAILS, and not by accident: taxcalc-ai is a path dependency
# declared in [tool.uv.sources], that table is uv-local and never reaches wheel metadata, so the
# wheel carries a bare `taxcalc-ai` requirement no index can satisfy. Build the sibling's wheel
# and point pip at it. This is what puts both console scripts on $PATH:
(cd ../taxcalc-ai && uv build)
pipx install ./dist/taxcalc_mcp_server-0.1.0-py3-none-any.whl \
      --pip-args="--find-links ../taxcalc-ai/dist"
which taxcalc-mcp-server taxcalc-mcp-server-sse
# Claude Desktop therefore launches `uv run --directory <repo>/taxcalc-mcp-server --frozen
# taxcalc-mcp-server` rather than `uvx taxcalc-mcp-server`: running from the project directory
# is what lets uv read pyproject.toml, honour the path source and pin from uv.lock. See the
# committed configs/claude_desktop_config.json and mcp.json, which both explain it in place.

# Confirm the four tool spans reach LangSmith. Each @traceable handler opens one `chain` run in
# the project named by TAXCALC_MCP_LANGSMITH_PROJECT (default `taxcalc-mcp-server` - note the
# settings prefix; the bare LANGSMITH_PROJECT in .env.example does not change it).
LANGSMITH_TRACING=true uv run python -m taxcalc_mcp_server.scripts.replay \
      --fixtures tests/fixtures/ --repeats 2
uv run python -c "from langsmith import Client; \
      print({r.name for r in Client().list_runs(project_name='taxcalc-mcp-server', limit=50)})"
# -> {'orders.get_order', 'orders.create_refund', 'llm.chat', 'rag.retrieve_and_generate'}
```

**Local-environment note, no repo change** (same Zscaler class as the W5 D4 and observability
notes above): behind TLS interception the LangSmith SDK cannot verify `api.smith.langchain.com`
— the CA is in the macOS keychain, not in `certifi`'s bundle — and the failure mode is the one
that matters: `list_runs` raises a loud `SSLError`, but the **trace export is a background
thread and fails silently**, so the replay exits 0, prints its latency table, and lands nothing.
A green run is not evidence the spans arrived; the project listing is. Build a bundle once and
point both variables at it:

```bash
security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain > /tmp/roots.pem
security find-certificate -a -p /Library/Keychains/System.keychain >> /tmp/roots.pem
cat "$(python -c 'import certifi;print(certifi.where())')" /tmp/roots.pem > /tmp/ca-bundle.pem
export SSL_CERT_FILE=/tmp/ca-bundle.pem REQUESTS_CA_BUNDLE=/tmp/ca-bundle.pem
```

CI is unaffected — GitHub runners have no interception — and both tiers set
`LANGSMITH_TRACING=false`, so no build depends on the vendor being reachable.

## Week 7 Day 5 — Multi-Agent Capstone: LangGraph three-node + supervisor + Postgres checkpointer + SSE + eval gate

W7 D3 built retrieval. W7 D4 published it behind MCP. W4 D4 built a React client that streams.
Today all of it lands behind **one running multi-agent service**: a new sibling project
[`taxcalc-agent-svc/`](taxcalc-agent-svc/) hosting a three-node LangGraph whose only tool surface
is the W7 D4 MCP server, whose only retriever is the W7 D3 sidecar, and whose answers stream back
into the W4 D4 `useChat` hook. Authoring transcripts in
[`taxcalc-agent-svc/PROMPT_JOURNAL.md`](taxcalc-agent-svc/PROMPT_JOURNAL.md); the consumer's-eye
view is in [`taxcalc-ai/PYTHON.md`](taxcalc-ai/PYTHON.md#what-w7-d5-adds); the on-call view is in
[`taxcalc-agent-svc/RUNBOOK.md`](taxcalc-agent-svc/RUNBOOK.md).

**The complexity is in the topology, not in the bodies.** Every node body is twenty lines that
delegate to something already built and already tested. What is new — and what this day is
actually about — is the composite contract around them: typed state with reducers, a supervisor
as the single policy point, per-node deadlines, two independent runaway caps, a durable
checkpointer, structured output at the end, end-to-end tracing, per-agent cost attribution, a
trajectory eval gate, and a GitOps deploy with a budget hard cap. Ticked independently they are
twelve checkboxes; missing any one turns a green build into a runaway loop, a double-debited
refund, a checkpoint that never resumes, or a synthesis that fabricates citations.

| Artefact | What it is | Why it is shaped that way |
|---|---|---|
| [`state.py`](taxcalc-agent-svc/src/taxcalc_agent_svc/state.py) | `AgentState` TypedDict; `operator.add` on `docs`/`cost_usd_e5`/`visited_nodes`, a key-wise merger on `tool_results` | **The silent failure this closes.** The supervisor fans out to both workers in the same super-step, and LangGraph's default channel is last-write-wins — so a bare `docs: list[dict]` lets whichever leg finished second erase the other's contribution, with no exception and no log line. `operator.add` cannot be used on `tool_results`: `dict + dict` raises `TypeError` at fan-in, at runtime. This is also the one module without `from __future__ import annotations`, so the reducers stay *readable* off `__annotations__` rather than becoming `ForwardRef`s only a `get_type_hints` incantation can inspect. |
| [`graph.py`](taxcalc-agent-svc/src/taxcalc_agent_svc/graph.py) — `supervisor` | A named node returning `list[Send]`, not a conditional edge | It is keyword routing today, which looks like it belongs inline. It is a node because of what lands here next: per-tenant rate limits, tenant gates, and the cost check that decides a plan is too expensive *before* paying for it. Those are policy and need one home. On an **empty plan it defaults to retrieval**, never to an empty fan-out — a question the router does not understand should be grounded, and routing nowhere reliably produces a confident, well-formed, entirely ungrounded answer. |
| [`nodes/_deadline.py`](taxcalc-agent-svc/src/taxcalc_agent_svc/nodes/_deadline.py) | `@deadline(seconds, sentinel)` applied **beneath** `@traceable` | The ordering is load-bearing and was **measured, not reasoned about**. `asyncio.wait_for` schedules its argument as a Task, which runs in a *context copy*; with `@deadline` outermost the node's run tree dies with the cancelled task and `get_current_run_tree()` in the handler returns the **root** `chat_request` run. Probing both orders under a live tree: `traceable` outermost tags `retrieval_agent`, `deadline` outermost tags `chat_request`. The second is worse than no tag — the LangSmith query meant to isolate slow *nodes* returns slow *requests*, and the responsible node is invisible in both. |
| [`nodes/api.py`](taxcalc-agent-svc/src/taxcalc_agent_svc/nodes/api.py) | Catalogue discovery via `session.list_tools()`; tenancy and UUID5 idempotency injected as **schema-declared arguments** | `ClientSession.call_tool` in mcp 1.x has **no `headers` parameter**, and D4 hardens every tool schema against extra keys — so the obvious header approach raises `TypeError` and the obvious blind injection would be rejected. Both point at the same answer: read each tool's published `inputSchema`. Introspecting the live D4 catalogue, all four tools declare `tenant_id` and exactly one — `orders.create_refund` — declares `idempotency_key`, so the schema *is* the instruction. Tenancy is **overwritten after the model speaks**: a model that read a document naming another tenant must not be able to reach it. |
| [`budgets.py`](taxcalc-agent-svc/src/taxcalc_agent_svc/budgets.py) + `recursion_limit` | Two caps, neither subsuming the other | `recursion_limit` bounds **turns**, so it catches a loop that spends nothing. `BudgetGuard` bounds **dollars**, so it catches a run that is progressing but expensive — twenty-four legitimate super-steps each making a 4,000-token call is inside any turn limit and outside any sane budget. Both directions are tested against each other. Money is an `int` in 1e-5 USD minor units: a run summing forty binary fractions accumulates error in the very number the ceiling is compared against, in a direction nobody controls, and a budget that can be crossed without firing is not a budget. |
| [`deps.py`](taxcalc-agent-svc/src/taxcalc_agent_svc/deps.py) | Per-request dependencies on `config["configurable"]`, **not** in the state | Every state slot is msgpack-serialised into a checkpoint row on every super-step. Verified directly: `JsonPlusSerializer().dumps_typed({"sess": socket()})` raises `TypeError: Type is not msgpack serializable`. An MCP `ClientSession` owns exactly such a live transport, so a `__mcp_session` state slot takes down every request the moment a real checkpointer is attached — and passes cleanly in any test that compiled without one, which is the worst possible place for the difference to appear. |
| [`sse.py`](taxcalc-agent-svc/src/taxcalc_agent_svc/sse.py) | `astream_events(v2)` → `0:`/`2:`/`3:`, with `GraphRecursionError` and `BudgetExceeded` on **distinct** codes | They demand opposite responses. A recursion breach is a *bug* — the graph looped and will loop again. A budget breach is a *limit* — the run was legitimate and an operator may simply raise the ceiling. Collapsing both into "something went wrong" costs the reader the one fact that decides what to do next. Errors are emitted **into** the stream rather than raised out of it: past the first frame the 200 and the headers are already on the wire, and raising there truncates the body into a bare connection drop with no reason attached. |
| [`evals/trajectory.py`](taxcalc-agent-svc/evals/trajectory.py) | 20 scenarios; trajectory ≥ 0.70, faithfulness ≥ 0.85, cost regression ≤ 15% | Three gates because each catches what the others cannot. Trajectory catches routing regressions — a supervisor that routed everything to both workers produces perfectly good answers at twice the cost, and faithfulness alone would call that healthy. Cost catches the change that improves both by spending three times as much: a prompt stuffing the whole corpus into context scores *better* on faithfulness while tripling the bill, and no quality metric will ever object. The match is **subset, not equality**, so a graph that grows a node does not fail twenty scenarios for doing more work on the way to the same answer. |
| [`cfn/agent-svc-budget.yaml`](taxcalc-agent-svc/cfn/agent-svc-budget.yaml) | `AWS::Budgets::BudgetsAction`, `APPLY_IAM_POLICY`, `AUTOMATIC` at 100% | The third layer of the same defence, and the only one that sees a slow leak: a per-request ceiling cannot detect a million individually-cheap requests. `AUTOMATIC`, not `MANUAL` — a cap awaiting human approval on the Saturday it exists for is the same as no cap. The cost is a self-inflicted outage at 100% of budget; that trade is made knowingly and the recovery is in the runbook. |

### Two defects the deployment rehearsal found, which testing did not

Standing the service up against a real Argo CD and a real cluster found two things that 123
passing tests did not, because both are properties of *deploying* rather than of running.

**The service could not start unless every dependency was already up.** The first lifespan opened
the MCP session and the Postgres checkpointer eagerly and let either failure propagate — which
under Kubernetes is a process that exits before it listens, so a briefly-unreachable dependency
means `CrashLoopBackOff` with exponential backoff long after the dependency returns. It also
contradicted this service's own `/healthz` docstring ("a health check that depends on every
downstream turns one dependency's blip into a cascading restart") one layer up, where no probe
configuration could soften it. [`runtime.py`](taxcalc-agent-svc/src/taxcalc_agent_svc/runtime.py)
now opens both lazily behind a lock and a retry, and splits the probes: `/healthz` is liveness and
reaches nothing; `/readyz` is readiness and is gated on the **checkpointer alone**, because a
docs-only question routes `retrieval_agent -> synthesis_agent` and touches no tool — refusing that
traffic because a different dependency is down throws away working capacity.

**The image shipped ~4.7 GB of CUDA libraries to a CPU-only service.** `torch` arrives
transitively through the W7 D3 reranker, and PyPI's Linux wheel bundles the entire NVIDIA CUDA
runtime, which a service running on CPU nodes will never load. Measured rather than suspected —
the macOS virtualenv is 1.2 GB because PyPI's macOS wheel is already CPU-only, while the Linux
image was **9.19 GB**:

| | image size | `nvidia-*` packages in the lock |
|---|---|---|
| unpinned (PyPI default) | 9.19 GB | 43 |
| PyTorch CPU index, Linux only | **4.48 GB** | **0** |

A 51% reduction, off every pull, every rollout and every image scan. `[[tool.uv.index]]` with
`explicit = true` so the partial mirror cannot silently serve unrelated packages, and the source
carries a `sys_platform == 'linux'` marker so macOS keeps PyPI's wheel. `torch` had to be declared
as a direct dependency for the pin to bind at all — `[tool.uv.sources]` applies only to direct
dependencies, so without that line the pin resolves in 28 ms and changes nothing, which is exactly
what it did on the first attempt.

### The deployment rehearsal, and the deadlock it found

Both GitOps claims were exercised against a real Argo CD rather than asserted. The Application
deployed differs from [the committed one](taxcalc-agent-svc/argo-apps/taxcalc-agent-svc.yaml)
only in `repoURL` and `project`; auto-sync, prune, self-heal and `ApplyOutOfSyncOnly` are as
committed.

```
$ kubectl -n argocd get app taxcalc-agent-svc
NAME                SYNC STATUS   HEALTH STATUS
taxcalc-agent-svc   Synced        Healthy

$ kubectl -n taxcalc-svc exec deploy/taxcalc-agent-svc -- ... /healthz /readyz
/healthz -> 200 {'status': 'ok', 'service': 'taxcalc-agent-svc', 'version': '0.1.0'}
/readyz  -> 200 {'graph': 'up', 'mcp': 'down', 'version': '0.1.0'}
```

That second line is the readiness split working in production conditions: **the pod is Ready
while the MCP server is unreachable**, because a docs-only question needs no tool.

**And the deployment found a deadlock in the fix above.** `/readyz` deliberately does not open
connections — a probe that did would hammer a dependency every few seconds precisely when it is
already unwell — so the graph was only ever opened by an arriving request. But Kubernetes keeps
an unready pod out of the Service's endpoints, so no request can arrive. Readiness waited on
traffic, traffic waited on readiness, and the pod sat at 503 indefinitely with Postgres healthy
beside it. The fix is a background retry owned by the lifespan, which keeps the probe read-only
and still converges; `tests/test_app.py` pins it with a dependency that fails once and then
succeeds.

**Rollback, rehearsed and timed** — full record in
[`RUNBOOK.md`](taxcalc-agent-svc/RUNBOOK.md#rehearsal-record--2026-09-20-k3d-lab-cluster):

| step | commit | wall clock |
|---|---|---|
| roll forward: CI-style tag bump `v1` → `v2` | `922b3e3` | **65 s** |
| roll back: `git revert` of the bump | `e6dd102` | **310 s** |

The asymmetry is the finding. Both are one commit and one image swap; the difference is entirely
Argo CD's poll interval — a revert pushed just after a poll waits out the full 180 s
`timeout.reconciliation` before the repo-server even sees the commit. So the runbook now says not
to rely on auto-sync during an incident: push the revert, then `--hard-refresh` and `sync` rather
than waiting five minutes for a tool to notice.

### The BudgetAction, and why the emulator that "verified" it proves nothing

The CloudFormation budget is the one artefact that cannot be exercised locally — firing it needs
an AWS account and a month of real spend. The obvious substitute is
[floci](https://github.com/floci-io/floci), the local AWS emulator this repo already uses for
exactly this gap (W5 D4, W6 D1). **It is worthless here, and worse than worthless because it
looks convincing.** Measured against floci 2.0.1:

| probe | floci |
|---|---|
| `aws budgets describe-budgets` | `UnknownOperationException` |
| `aws budgets describe-budget-actions-for-budget` | `UnknownOperationException` |
| `cloudformation deploy` of the committed template | **`CREATE_COMPLETE`** |
| `cloudformation deploy` of a template with the four property names `cfn-lint` rejects | **`CREATE_COMPLETE`** |
| `cloudformation validate-template` on that broken template | accepted, silently |

floci implements no Budgets service, so its CloudFormation treats `AWS::Budgets::*` as an opaque
passthrough and reports success for anything. A green floci deploy proves the template is
well-formed YAML whose parameters, `!Ref`s, `Outputs` and `DependsOn` resolve — and **nothing**
about whether the resources are valid. That is this repo's recurring lesson in its third
instance: *floci's most confident answer was its wrongest.*

So the authority is AWS's **own published resource provider schemas**, the artefacts
CloudFormation validates against server-side, which `cfn-lint` bundles — the same move as putting
the W5 D4 template through AWS's `samtranslator` offline when floci disagreed. Read straight out
of that schema:

```
AWS::Budgets::BudgetsAction
  ActionThreshold : required ['Value','Type'],   additionalProperties: false
  Subscriber      : required ['Type','Address'], additionalProperties: false
```

while the sibling `AWS::Budgets::Budget` spells the same concept `SubscriptionType` — which is
precisely the mistake the template made in four places.

[`scripts/verify-budget-stack.sh`](taxcalc-agent-svc/scripts/verify-budget-stack.sh) encodes
this and runs in the PR tier. Its second step is the one that matters: it **reintroduces the
four wrong names and asserts `cfn-lint` rejects them**, so the gate is proven able to fail rather
than merely observed passing. It also fails loudly if the broken variant ever comes out identical
to the template, since a negative control that has silently stopped breaking anything is the
worst kind of green.

**And the reason nothing could verify the DENY policy: it did not exist.** The template
referenced `arn:aws:iam::123456789012:policy/DenyLlmProxyInvoke` as a parameter default, and that
policy document was nowhere in this repository — so the hard cap pointed at something nobody had
written, let alone reviewed. It is now an `AWS::IAM::ManagedPolicy` in the template, which makes
it a reviewable artefact and makes its decision computable.

**Which surfaced the finding that matters more than any of this.** The agent calls
`api.anthropic.com` **directly** — `AsyncAnthropic(api_key=...)` in all three node bodies, no base
URL override — so its model spend never crosses an AWS-controlled surface, and *no IAM policy can
block an outbound call to a third party*. A cap denying `execute-api:Invoke` on the llm-proxy was
guarding a path this service does not use. What AWS does control is the **key**: ESO reads it from
Secrets Manager under the agent's IRSA role, so the enforceable statement is
`secretsmanager:GetSecretValue`, which stops any restarted or newly-scheduled pod from obtaining
one. A pod already running keeps spending — which is not a gap in the policy but the reason the
in-process `BudgetGuard` exists.

[`scripts/simulate_budget_deny.py`](taxcalc-agent-svc/scripts/simulate_budget_deny.py) evaluates
the decision offline, following the precedent of
[`scripts/oidc-trust-simulate.py`](scripts/oidc-trust-simulate.py) — reproduce IAM's procedure
rather than mock it, because *an emulator does not evaluate policy at all*. floci answers
`UnsupportedOperation` for `simulate-custom-policy`, and the W6 D1 experiment caught it issuing
working credentials for a **forged** token. Five decisions, three of them negative controls:

```
== the BudgetAction is configured as a cap, not as a notification ==
  [ok ] threshold 100% ACTUAL, AUTOMATIC approval, APPLY_IAM_POLICY,
        attaching the policy this template itself defines
== the attached policy's decisions ==
  [ok ] secretsmanager:GetSecretValue -> Deny       THE cap
  [ok ] execute-api:Invoke            -> Deny       the proxy path, once routed
  [ok ] secretsmanager:GetSecretValue -> NotDenied  (unrelated secret still readable)
  [ok ] sqs:ReceiveMessage            -> NotDenied  (unrelated services keep working)
  [ok ] secretsmanager:DescribeSecret -> NotDenied  (scoped to the VALUE, not metadata)
```

Both new gates were proven able to fail before being trusted: flipping `ApprovalModel` to
`MANUAL` reports *"the cap would wait for a human and is therefore advisory"*, and widening the
deny to `secretsmanager:*` trips the metadata negative control.

What genuinely remains unverified, printed by the script rather than buried: that AWS accepts the
stack, that the Budgets **service** fires at 100% (the *configuration* that decides whether it
would is now asserted), and that account SCPs do not alter the decision.

### Four things measured rather than assumed — each one changed the code

Every one of these produces **no exception on the happy path**, which is why they are recorded
rather than quietly fixed.

1. **The decorator order decides which span gets blamed.** The brief's reference snippet lists
   `@deadline` above `@traceable`; its prose says to apply `@deadline` *before* `@traceable`,
   which — decorators applying bottom-up — is the opposite. Both orders produce the sentinel and
   both tag some run, so the call site cannot distinguish them. A probe delegating to langsmith's
   own `get_current_run_tree` under a live tree settles it: `traceable` outermost tags
   `retrieval_agent`; `deadline` outermost tags `chat_request`. The prose is right.

2. **`PostgresSaver` cannot serve async nodes.** Every node body here is `async`, so every call
   site is `ainvoke`, so LangGraph drives the checkpointer's *async* interface — which the
   synchronous saver inherits from `BaseCheckpointSaver` as `raise NotImplementedError`. It
   fails inside `AsyncPregelLoop.__aenter__` before any node runs. `AsyncPostgresSaver` is not a
   preference here; the sync one does not work at all.

3. **A stable eval `thread_id` makes the cost gate fire forever.** Found by the gate failing a
   build in which nothing had changed. `thread_id=f"eval-{qid}"` is stable across runs, the
   checkpointer persists under it, and `cost_usd_e5` carries `operator.add` — so the second run
   of the suite resumed the first and reported exactly double: 508 then 1016 (1e-5 USD), a +100%
   "regression" caused entirely by the eval talking to itself. Namespacing the thread per run
   gives 508, 508, 508 across three consecutive runs.

4. **`cfn-lint` caught four property names the reference template had wrong.**
   `AWS::Budgets::BudgetsAction` spells its threshold `Value`/`Type`, not
   `ActionThresholdValue`/`ActionThresholdType`, and its subscribers take `Type` where the
   sibling `AWS::Budgets::Budget` takes `SubscriptionType`. Two resources in one service
   spelling the same concepts differently — found in the fast tier rather than as a stack
   rollback, which is the entire argument for linting infrastructure in CI.

### Where this deliverable departs from the letter of its spec

Six places, all forced by the libraries as they actually behave. The first five are above or in
the journal; the sixth is procedural.

* `state["__mcp_session"]` → `config["configurable"]`, because state is checkpointed and a live
  session is not serialisable.
* `PostgresSaver` → `AsyncPostgresSaver`, and `graph.invoke` → `await graph.ainvoke`.
* `session.call_tool(..., headers=...)` → schema-driven argument injection, because mcp 1.x has
  no such parameter.
* `state["__visited_nodes"]` → an explicit `visited_nodes` state slot with a reducer, because
  LangGraph has no such key and the nearest equivalent is empty without a checkpointer.
* `@deadline` is applied **beneath** `@traceable`, following the brief's prose rather than its
  snippet, for the reason measured above.
* **The branch was cut from `w7d4-implementation` rather than `main`, and has since been rebased
  onto `main`.** When this work started, `main` was at W7 D2 and both W7 D3 and W7 D4 were still
  open PRs — so branching from `main` would have produced a tree with no `taxcalc-mcp-server/`
  and a W7 D2 sidecar, which is not a tree this deliverable can be built in. D4 merged (PR #61)
  while D5 was being written, so the deviation resolved itself: the branch is now rebased onto
  `main` and adds exactly the four D5 commits, with no content from D3 or D4 in its diff.

### Running it

```bash
# W7 D5 - the multi-agent service. A separate uv project; `cd` out of the others first.
cd taxcalc-agent-svc && uv sync

# The fast gates, in the order CI runs them.
uv run ruff check
uv run mypy --strict src/ tests/ evals/
uv run pytest -v -m "not e2e" --cov=src --cov-fail-under=70

# The container-backed durability proof: kills the first connection pool entirely before
# building the second, so nothing but the database carries state across. Needs Docker.
uv run pytest -v -m e2e

# The trajectory eval. `--offline` stubs the node BODIES only - the supervisor, the fan-out
# plan and the reducers are production code, so all twenty scenarios exercise real routing.
# Faithfulness is not scorable against a canned answer, so the gate says NOT MEASURED out loud
# rather than rendering an unmeasured metric as a green tick.
uv run python -m taxcalc_agent_svc.scripts.eval --offline --gate --allow-unmeasured-faithfulness

# The full gate, with RAGAS scored on real answers. Needs an Anthropic key, the MCP server
# reachable over SSE, and the sidecar's Postgres + Redis.
uv run python -m taxcalc_agent_svc.scripts.eval --gate

# Prove the checkpointer against a throwaway Postgres: invoke twice on one thread_id and count
# the rows. The second run's visited_nodes comes back LONGER than the first's - the reducer
# appending onto the persisted list is the resume proving itself.
docker run -d --name pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16-alpine
TAXCALC_AGENT_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  uv run python -m taxcalc_agent_svc.scripts.smoke --offline --thread-id t1

# Serve it. The lifespan opens one MCP session and one checkpointer pool for the process.
uv run taxcalc-agent-svc

# The image. Context is the REPOSITORY ROOT - two path dependencies live beside this project
# and a context rooted here could not see either.
docker build -f taxcalc-agent-svc/Dockerfile -t taxcalc-agent-svc:dev .

# The infrastructure lints CI runs in the fast tier.
uv tool run cfn-lint taxcalc-agent-svc/cfn/agent-svc-budget.yaml
docker build --check -f taxcalc-agent-svc/Dockerfile .

# The money and durability greps.
grep -RIn ': float ' taxcalc-agent-svc/src/taxcalc_agent_svc/budgets.py   # -> no output
grep -RIn 'MemorySaver' taxcalc-agent-svc/src/                            # -> no output
```
