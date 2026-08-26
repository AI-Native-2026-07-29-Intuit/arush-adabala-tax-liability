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

## Build and Test

```bash
./gradlew build   # compile and run all checks
./gradlew test    # run the JUnit 5 test suite
```

```bash
cd taxcalc-web
pnpm install
pnpm lint && pnpm typecheck && pnpm test && pnpm build   # same gate as .github/workflows/web-ci.yml
pnpm dev                                                  # http://localhost:5173/#/taxpayers/stub-id-1
```