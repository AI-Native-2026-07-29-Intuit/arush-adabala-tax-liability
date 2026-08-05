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

## Build and Test

```bash
./gradlew build   # compile and run all checks
./gradlew test    # run the JUnit 5 test suite
```