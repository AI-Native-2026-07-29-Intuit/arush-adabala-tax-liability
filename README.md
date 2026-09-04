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
`liabilityAmount` are typed `number`, not the BigDecimal-as-string
convention the rest of this codebase uses, because no `@JsonFormat` is
configured on the backend and Jackson serializes `BigDecimal` as a JSON
number by default. A 404 resolves to `null` data instead of throwing, so
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

**No `taxcalc-api/` subdirectory, again.** Every path this deliverable's own reference spec names under `taxcalc-api/.github/...`/`taxcalc-api/infra/...` lives at this repo's root instead — `.github/PIPELINE.md`, `infra/oidc/`, same reasoning `docker/SECURITY.md` already documents for `Dockerfile`/`.hadolint.yaml`. **Blacksmith runners, not `ubuntu-24.04`**: the reference spec's generic runner would never even queue in this org after the GitHub Actions billing block W5 D2 already worked around — every new workflow here uses `blacksmith-2vcpu-ubuntu-2204`, matching every other workflow in `.github/workflows/`. **JDK 17, not 21**, in the new composite action — `build.gradle`'s toolchain pin, same constraint the Dockerfile's `builder` stage already documents.

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

One trap worth recording, because it looks like the obvious next step: **do not `gh variable set AWS_ACCOUNT_ID` to the emulator's `000000000000`.** That variable is what gates `_build-and-push.yml`'s AWS steps, so setting it would un-gate them in CI — and a GitHub runner cannot reach a floci on a laptop, so `main` would start failing at the assume-role step. The emulator is a local verification target, not a CI backend.

**The same workflow's third probe returned a null result, and is reported as one.** `sts:AssumeRoleWithWebIdentity` needs no AWS credentials, so a repo with no AWS account can still ask the real endpoint whether it accepts a real GitHub token; the call must fail (no role exists), making the *error code* the signal, with a tampered-signature control run to tell "AWS verified the signature" apart from "AWS never looked". Real AWS returned `InvalidIdentityToken` for **both** the genuine and the deliberately-corrupted token — so the probe demonstrates nothing about signature verification, most likely because STS rejects on the unresolvable account/provider before ever inspecting the JWT. The workflow prints that verdict explicitly rather than reporting a green run as a pass. **"Will GitHub's real token be accepted by AWS?" therefore remains genuinely open, and needs an AWS account** — it is the one question in this deliverable with no offline substitute.

Every action this deliverable added is pinned to a full commit SHA with a version comment (`# v4.5.0`) — `grep -RIn '@v[0-9]\+\.\?[0-9]*\.\?[0-9]*$'` across `ci.yml`, `_build-and-push.yml`, `deploy-prod.yml`, and `setup-build/action.yml` returns zero matches — and `.github/dependabot.yml` groups weekly SHA-bump PRs by `actions/*`/`aws-actions/*`/`docker/*` so they land as three PRs, not fifteen. **Known, deliberate gap:** that same grep against the *whole* `.github/` tree is not zero — `docker.yml`, `compose-ci.yml`, `k8s-ci.yml`, `observability.yml`, `serverless.yml`, and `web-ci.yml` (all from earlier, already-graded days) still pin some actions by tag. Rewriting six unrelated workflow files' pins is a real, separate-blast-radius change (a wrong SHA silently breaks a day this deliverable doesn't own) — left as a follow-up rather than a silent drive-by, consistent with how every other day in this README calls out what it deliberately left untouched. The `github-actions-author` Claude Skill this deliverable's lesson names for the scaffold-then-audit step was not available in this session's tool listing; the workflow YAML here was hand-authored directly against the cohort checklist instead, with the checklist's own named "common quirks" (`@v4` with no SHA, a redundant `actions/cache` step alongside `setup-java`'s built-in `cache: gradle`) checked for explicitly — see `.github/PIPELINE.md`'s "AI-tool review note".

**Verified live**, not just read: `actionlint .github/workflows/*.yml` (run via the pinned `rhysd/actionlint` container, matching this repo's existing preference for pinned containers over ad-hoc downloaded binaries — see W5 D5's Sloth section) now exits `0` with **no findings at all, unfiltered, across every workflow in the repo**. Getting there took two fixes rather than an excuse. Seven `runner-label` findings (one per Blacksmith-runner job, tree-wide) were being mentally discounted as "expected noise" — which is exactly how a real finding eventually gets skimmed past, and it made this deliverable's own Done-When check unsatisfiable by construction; `.github/actionlint.yaml` declares the two Blacksmith labels, which is actionlint's own documented fix for a self-hosted-class label and what its error message tells you to do. The eighth was a genuine pre-existing `shellcheck` SC2034 in `observability.yml` (W5 D5): its image-import retry loop is a copy of `k8s-ci.yml`'s that dropped the `echo "Import attempt ${attempt}..."` line, leaving the loop variable unused — restored, which both silences the warning and puts the two sibling loops back in parity, rather than renaming the variable to `_` and losing the log line. `ruby -ryaml` round-trip-parses every new YAML file. Both trust-policy JSONs parse cleanly with Python's `json.load`. `shellcheck` is clean on `docker-oidc-bootstrap.sh`. The PR's own `Build + test (taxcalc-api)` check went green end to end against the real Testcontainers suite (~5 minutes), and `call-build-and-push` correctly reported `skipping` on the PR event (it only fires on push to `main`).

**Also done, not just written:** the `dev` and `prod` GitHub Environments now exist for real (created via the GitHub API), each with its deployment-branch policy restricted to `main` only.

**Two pieces of this deliverable are genuinely not done, and one of them cannot be.** Adding `prod`'s required-reviewer rule is a permission-granting repo-admin action, left for a human in the GitHub UI — which happens to match the lesson's own framing exactly ("the UI configuration is not in version control... screenshot it for the PR"). **Branch protection on `main` requiring the `build-test` check is not merely undone — it is unavailable on this repository's current plan.** Both `GET /repos/{owner}/{repo}/branches/main/protection` and the newer `GET /repos/{owner}/{repo}/rulesets` return `403: "Upgrade to GitHub Pro or make this repository public to enable this feature."` on this private repo, so neither the classic branch-protection rule nor a repository ruleset can be created by anyone — API or UI — until the org upgrades or the repo is made public. That is worth recording rather than leaving as an unexplained unchecked box: the deliverable's third Done-When condition is blocked on billing, not on work, and it is the second time this curriculum has hit a plan-level GitHub constraint in this repo (the W5 D2 Actions billing block that forced every workflow onto Blacksmith runners is the first, and is why `ci.yml` cannot use the spec's `ubuntu-24.04` either). `.github/PIPELINE.md`'s "What is NOT in this repo" section carries both items and their exact remediation.

## Build and Test

```bash
./gradlew build   # compile and run all checks (the Spring Boot service)
./gradlew test    # run the JUnit 5 test suite
```

```bash
# The W5 D4 Lambda is a separate Maven build over com.uptimecrew.tax_liability.lambda only.
mvn -B -ntp test                          # JUnit 5 + Mockito + AssertJ, no AWS needed
sam validate --lint --region us-east-1    # cfn-lint over the transformed template
sam build --use-container                 # build inside the AWS Lambda java21 parity image
sam local invoke TaxpayerLookupFunction --event events/get-taxpayer.json
```

```bash
cd taxcalc-web
pnpm install
pnpm exec playwright install chromium   # once, before the first `pnpm check` or `pnpm e2e`
pnpm check                              # tsc --noEmit && eslint . && vitest run --coverage && playwright test - same gate as .github/workflows/web-ci.yml
pnpm dev                                # http://localhost:5173/login
```