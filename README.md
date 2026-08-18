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

## Build and Test

```bash
./gradlew build   # compile and run all checks
./gradlew test    # run the JUnit 5 test suite
```