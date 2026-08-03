# arush-adabala-tax-liability
Bootstrap the Tax-Liability Domain

## Tech Stack

- Java 17 (Gradle toolchain)
- Gradle (wrapper included, no local Gradle install required)
- JUnit 5 (Jupiter) for tests

## Package Layout

All classes live under `com.uptimecrew.tax_liability`:

- `model` — domain types: `IncomeEvent`, `IncomeEventDraft`, `IncomeSource`, `Deduction`, `TaxBracket`
- `service` — behavior over the domain model: `BracketResolver` (interface, returns `Optional<TaxBracket>`), `FederalBracketResolver` (implementation), `BracketRegistry` (queryable, immutable store of `TaxBracket` records)

## Build and Test

```bash
./gradlew build   # compile and run all checks
./gradlew test    # run the JUnit 5 test suite
```