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

**Six real problems found by running the verification commands rather than reading them, each fixed with a comment in place:**

1. **`sam build` built the wrong project.** SAM picks its Java workflow from the build file it finds in `CodeUri`, and it checks `build.gradle` **before** `pom.xml`. This repo's root has both, so `sam build` silently selected `JavaGradleWorkflow` and started building the whole Spring Boot application, failing on that application's own Lambda-irrelevant dependency graph. Pointing `CodeUri` at the pre-built jar instead — as the reference template does — fails differently and just as hard: SAM treats `CodeUri` as a *directory* and reports `Gradle build file not found: .../taxcalc-taxpayer-lookup-1.0.0.jar/build.gradle`. Fixed with `Metadata: { BuildMethod: makefile }` and a `build-TaxpayerLookupFunction` target, taking the workflow guess out of SAM's hands entirely.
2. **A jar at the root of the deployment zip is never on the classpath.** The Makefile target copies the shaded jar into `$(ARTIFACTS_DIR)/lib/`, because the Java runtime puts `/var/task/lib/*.jar` on the classpath but ignores a jar sitting loose at the zip root — a function that would deploy perfectly cleanly and then fail every invocation with `ClassNotFoundException`.
   *Consequence of `CodeUri: .` worth knowing before you hit it:* `sam build` copies the whole CodeUri tree into a scratch directory (and, under `--use-container`, into the build container). On a developer laptop this repo's working tree is ~572MB and that copy dominates the build — but it is entirely gitignored local state (`taxcalc-web/node_modules` 429MB, `build/` 122MB, `.gradle/`, `target/`); the tracked content SAM actually needs is **1.6MB**, so a fresh `actions/checkout` in CI copies ~1.6MB.
3. **A DynamoDB failure escaped as an unhandled Lambda error.** Caught live by `sam local invoke`: the SDK's "security token is invalid" surfaced as a raw `DynamoDbException` stack trace instead of an HTTP response, and API Gateway renders that as an opaque 5xx with no body and — critically — no `x-correlation-id` header, losing the trace at exactly the point a caller needs it. `SdkException`/`IllegalStateException` now map to a 500 that carries the correlation id like every other response.
4. **The reference handler shape cannot survive `mvn test`.** `private static final DynamoDbClient DDB = DynamoDbClient.builder().build()` throws `SdkClientException: Unable to load region` at *class-initialisation* time anywhere `AWS_REGION` isn't set — every unit test, every CI runner. That's an `ExceptionInInitializerError` before a single assertion runs, taking down even the 400-path and correlation-id tests that never touch DynamoDB. `buildDynamoClient()` now reads `AWS_REGION` (always set by the Lambda runtime, so config stays externalised) and returns `null` rather than throwing when nothing resolves; `loadFromDynamo` fails fast with a clear message at first use instead.
5. **Excluding the SDK's default HTTP clients means you must name one.** `netty-nio-client` (an async event-loop group built at `build()` time) and `apache-client` are both excluded to keep INIT cheap for a handler that makes exactly one blocking `GetItem`; with neither present the SDK fails at build time with "Unable to load an HTTP implementation", so `UrlConnectionHttpClient.create()` is set explicitly.
6. **slf4j-simple can never satisfy `LogFormat: JSON`.** The first version of this deliverable used slf4j-simple with a `simplelogger.properties` redirecting to `System.out`, reasoning that the runtime would wrap whatever landed on stdout. That reasoning was wrong: Lambda emits structured-JSON application logs *only* for `LambdaLogger` or Log4j2, so the function would have reported `LogFormat: JSON` while every application line stayed plain text. Replaced with SLF4J-over-Log4j2 and the `aws-lambda-java-log4j2` appender — see the logging section below, including the two further silent failures that switch exposed.

**`AutoPublishAlias: live` is the half of SnapStart that nothing complains about when you omit it.** SnapStart only ever applies to *published versions*; without the alias the HttpApi integration targets `$LATEST`, which never has a snapshot, and the console cheerfully reports SnapStart "enabled" on a function that never restores from one.

**Logging goes through Log4j2, and that is load-bearing rather than a style choice.** Lambda emits structured-JSON *application* logs only for functions that log via `LambdaLogger` or Log4j2 — every other library, slf4j-simple included, is captured verbatim as plain text no matter what `LoggingConfig.LogFormat` says. This deliverable originally shipped slf4j-simple with a `simplelogger.properties` that redirected to `System.out` on the (wrong) theory that the runtime would wrap whatever appeared on stdout; the function would have advertised `LogFormat: JSON` while every application line stayed unstructured. It now uses SLF4J on top of Log4j2 with the `aws-lambda-java-log4j2` `<Lambda>` appender, which switches layout on the `AWS_LAMBDA_LOG_FORMAT` env var the runtime sets from the template. The correlation id moved from a `{}` placeholder in every message into **MDC**, so `JsonTemplateLayout` promotes it to a top-level field that Logs Insights can filter on rather than regex out of message text — cleared in a `finally` block, because execution environments and their threads are reused and a leaked id would mislabel the next request. Verified emitted (see below):

```json
{"timestamp":"2026-09-01T20:07:19.802Z","level":"INFO","message":"lookup hit taxpayerId=txp_synth_001 liabilities=1",
 "logger":"com.uptimecrew.tax_liability.lambda.TaxpayerLookupHandler","AWSRequestId":"55189a49-...","correlationId":"json-log-probe-99"}
```

Getting there took two fixes whose shared failure mode is silence — both leave the build green, the tests green, and only the deployed function broken:

1. **A split Log4j2 api/core pair.** `aws-lambda-java-log4j2:1.6.0` pulls `log4j-api:2.17.1` transitively, which Maven's nearest-wins resolution then paired with the declared `log4j-core:2.24.3`. Provider registration changed between those versions, so the two could not find each other and Log4j2 fell back to its internal SimpleLogger, announcing it only via one `StatusLogger` line on stderr. Fixed by importing `log4j-bom` so every log4j artifact, transitive included, lands on one version.
2. **The shaded jar had no Log4j2 plugin index.** Log4j2 resolves its plugins — every `PatternLayout` converter, every appender and layout, including `<Lambda>` — through a binary `Log4j2Plugins.dat` that each jar ships its own copy of; a plain shade keeps exactly one. The deployed function printed `Unrecognized conversion specifier` for `%d`, `%level`, `%msg` and friends and logged nothing useful. Fixed with `Log4j2PluginCacheFileTransformer` in the shade config. **`mvn test` structurally cannot catch this** — tests run against the unshaded classpath where every `.dat` is still separate, so it only appears once the shaded artifact actually runs.

The custom metrics (`TaxpayerLookupSuccess` / `TaxpayerNotFound`, namespace `TaxcalcDev`) are hand-written EMF lines on `System.out` rather than synchronous `cloudwatch:PutMetricData` calls — PutMetricData would add a network round trip to every invocation *and* force a second IAM permission onto an execution role this deliverable exists to scope down to one DynamoDB table. Powertools' metrics module was considered and not adopted, though the original reason given here (that it requires aspectj) was **wrong** and is corrected: Powertools v2 has a `MetricsBuilder`/`MetricsFactory` functional API needing no annotation and no weaving. The real reason is that it emits EMF through `System.out` exactly as this code does, so it buys validation helpers rather than a different delivery mechanism — not worth a dependency for two counters. Because hand-assembled JSON fails *silently* here — CloudWatch accepts a malformed EMF line as an ordinary log event and simply never publishes a metric, with nothing raising an error anywhere — `buildEmf` is split out and unit-tested: the payload is parsed and asserted to have `_aws` at the root, a dimension key resolving to a real member of the same object, and the metric name doubling as the value key.

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
| IAM | only `AWSLambdaBasicExecutionRole`, `Policies: null` | an inline policy with `dynamodb:GetItem/Scan/Query/BatchGetItem/DescribeTable` scoped to the table ARN **and** its `/index/*` ARN — zero `"*"` in either Action or Resource ✓ |
| `LoggingConfig` | provisioned as `Text` | `{"LogFormat": "JSON", "ApplicationLogLevel": "INFO", "SystemLogLevel": "WARN"}` ✓ |
| Alarm statistic | `ExtendedStatistic: None` | `ExtendedStatistic: p99`, `Statistic: null` ✓ |

So four of the five were emulator fidelity gaps and the template was right all along. The IAM row is worth reading closely — it is the graded `aws iam get-role-policy` artefact, generated by AWS's own policy-template catalog rather than by hand.

**The `LogFormat: JSON` row was then closed observably, not just on paper.** The `<Lambda>` appender switches on `AWS_LAMBDA_LOG_FORMAT` — the env var the real runtime sets from `LoggingConfig`, reserved on AWS but settable on the emulator. Setting it to `JSON` and invoking produced the exact documented envelope (quoted in the logging section above), which is what surfaced the two silent Log4j2 defects described there.

### Cold-vs-warm latency, measured for real (RIE), and what it does and does not prove

floci cannot measure this — but AWS's own **Runtime Interface Emulator** can, and it is a different tool entirely: `sam local start-lambda` runs the function inside `public.ecr.aws/lambda/java:21-rapid-arm64`, the real published runtime image, and emits genuine `REPORT` lines. Run with `--warm-containers EAGER` so the container is reused (without it every invocation gets a fresh container and *every* sample is cold — the first attempt here produced cold 632ms vs warm 609ms, which is the signature of that mistake, not a real result), 1 cold + 39 warm invocations against the `400` branch:

| | Duration |
|---|---|
| **Cold** — first invocation into a fresh JVM | **744.5 ms** |
| **Warm** — n=39: min / p50 / p90 / p99 | **1.14 / 3.74 / 5.86 / 19.22 ms** |
| cold ÷ warm p50 | **199×** |
| INIT-attributable delta (cold − warm p50) | **740.7 ms** |

Read precisely, because it is easy to overclaim:

- **This is not a SnapStart before/after.** The RIE has no snapshot/restore. What it quantifies is the *size of the prize*: ~741 ms of JVM start, class loading and static-initialiser work (SDK client, `ObjectMapper`, Log4j2 config) that SnapStart is designed to remove, measured in the real runtime image rather than guessed at.
- **The `400` branch was used deliberately** — it exercises JVM start, every static initialiser, MDC and response building with zero network I/O, so the cold number isn't polluted by a DynamoDB round trip. The corollary is that **warm p50 of 3.74 ms excludes the `GetItem`**; a real warm p50 on the 200 path will be higher.
- **The RIE's own `Init Duration` field is useless here** — it reports 0.03–0.10 ms because JVM initialisation is folded into the first invocation's `Duration`. That is why "cold" is defined above as the first invocation's `Duration`, not as `Init Duration`.
- **Against the deliverable's targets** (cold p99 < 600 ms, warm p50 < 60 ms): warm passes with a 16× margin. Cold at 744 ms does *not* — which is exactly the gap SnapStart exists to close, and exactly what cannot be confirmed without a real deploy.

**Still genuinely open — these need real AWS and nothing else will do:**

| Graded artefact | Why an emulator cannot stand in |
|---|---|
| SnapStart cold-start improvement | The measurement above sizes what SnapStart would remove, but the post-SnapStart cold number needs Firecracker restore on real AWS. |
| `list-metrics --namespace TaxcalcDev` | floci stores the EMF line as an ordinary log line and never parses it (`{"Metrics": []}`). The template-side risk is now closed though: AWS documents that *"Lambda doesn't double-encode any logs that are already JSON encoded"*, so `_aws` stays at the root under `LogFormat: JSON` — the earlier worry about the ALC envelope swallowing it was unfounded. |
| Alarm state / `describe-alarms` | Created, but with the p99 dropped and `INSUFFICIENT_DATA`. |
| CloudWatch `REPORT` lines *in the deployed log group* | floci emits none, so `sam-smoke.sh`'s check is skipped there via `EXPECT_RUNTIME_REPORT=false`. Partly closed though: the RIE **does** emit real `REPORT` lines locally (they are the source of the latency table above), so the format the script greps for is confirmed against the real runtime — only their delivery into CloudWatch Logs is unverified. |
| OIDC CI deploy | GitHub↔AWS STS trust plus a real Actions run; no local equivalent by construction. |

Two further floci quirks worth knowing before repeating this: its CloudFormation Outputs report the real-AWS-shaped `https://{id}.execute-api.{region}.amazonaws.com` hostname, which does not resolve locally — the API is actually served at **`http://localhost:4566/execute-api/{apiId}/{stage}`** (the LocalStack-style `/restapis/.../_user_request_/` and `*.localhost.localstack.cloud` forms all 404; cf. upstream issue [#1902](https://github.com/floci-io/floci/issues/1902)). And `get-template --template-stage Original` returns the *post*-transform template (`TaxpayerLookupFunction` has `Type: AWS::Lambda::Function`), so you cannot inspect what was actually submitted. To keep the smoke script usable in both worlds without a second drifting copy, `scripts/sam-smoke.sh` gained two overrides that both default to real-AWS behaviour: `HTTP_API_URL` (bypass Outputs resolution) and `EXPECT_RUNTIME_REPORT` (skip the REPORT assertion). Never set the latter to `false` for a real AWS run — that check is what catches a function and a log group that have drifted apart.

(Unrelated local-environment note, no repo change: this laptop sits behind Zscaler TLS interception whose root CA is in the macOS System keychain but in no JDK truststore, so Maven Central and Gradle only resolve with `-Djavax.net.ssl.trustStore` pointed at a keychain-derived store. GitHub Actions runners are unaffected.)

What *was* run and did pass, locally:

```bash
sam validate --lint --region us-east-1   # -> "template.yaml is a valid SAM Template"
mvn -B -ntp test                         # -> Tests run: 13, Failures: 0, Errors: 0
mvn -B -ntp package                      # -> target/taxcalc-taxpayer-lookup-1.0.0.jar (shaded, 10.6MB)
sam build                                # -> Build Succeeded (2m52s; the makefile build method)
sam local invoke TaxpayerLookupFunction --event events/get-taxpayer.json
# -> {"statusCode": 500, "headers": {"x-correlation-id": "local-smoke-corr-1"}, ...}
#    500 is the correct answer here: the handler loaded, the Handler string resolved, the
#    event's correlation id propagated, TAXPAYERS_TABLE was injected, and the GetItem was
#    genuinely rejected for want of credentials.
./gradlew compileJava compileTestJava    # -> BUILD SUCCESSFUL; the Gradle/Maven split holds
```

`sam build --use-container` is the one local command that does **not** pass here, and for a reason external to the code: it copies the source in, starts `public.ecr.aws/sam/build-java21`, and runs `make build-TaxpayerLookupFunction` correctly — then Maven inside the container cannot reach Maven Central, because the container's truststore has no Zscaler root CA (`PKIX path building failed ... unable to find valid certification path`). The build-method plumbing is therefore proven end to end inside the parity image; only dependency resolution through the corporate TLS proxy fails, which is not a condition GitHub Actions runners are under. `sam build` without `--use-container` uses the host Maven (pointed at a keychain-derived truststore) and succeeds.

```bash
./scripts/sam-deploy.sh    # sam validate --lint -> sam build --use-container -> sam deploy -> print Outputs
./scripts/sam-smoke.sh     # resolve HttpApiUrl from stack Outputs; known-good path + correlation-id echo, route-miss 404, CloudWatch REPORT check
sam delete --stack-name taxcalc-lambda-dev --region "$AWS_REGION"   # teardown is part of the deliverable, not an afterthought
```

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