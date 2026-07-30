# CLAUDE.md
## Conventions

Target jdk 17 or above. Reserve var for a few places and use it only when it makes sense (make it easier to read) – otherwise opt for explicit type in public API.

2. Money fields: all monetary values are declared as a `java.math.BigDecimal` with an explicit scale of 2, and rounding mode of HALF_UP (e.g. amount.setScale(2, RoundingMode.HALF_UP)). Do not use double or float to store money rounding error for calculating tax is unacceptable.

3. Identifiers: All ID fields are of type `String`: UUID v4 or a fake ID with some prefix (e.g. `"inc-synth-001"`) Avoid using `int` or `long` names, they easily lead to arithmetic and don't link to other systems.

Dates: java.time.LocalDate for calendar dates (when income was recorded), java.time.Instant for timestamps. Avoid using java.util.Date, java.sql.Date, or java.util.Calendar.

5. fields are defaulting to `private final`; classes are defaulting to `final` unless they are intended for extension. Use `Objects.requireNonNull` to assert that all constructor arguments are not null; use IllegalArgumentException to assert that all constructor arguments are not nonsensical (e.g., a negative amount, an empty string). Avoid Lombok @Data and other shortcuts (don't use them) explicitly write constructors, accessors, equals, hashCode and toString.

6. Tests: only use JUnit 5 (Test, BeforeEach, assertEquals, assertTrue, assertThrows). No JUnit 4 or no third-party assertion libraries.

7. Package root: all classes are grouped in the package `com.uptimecrew.tax_liability` (such as `com.uptimecrew.tax_liability.model`, `com.uptimecrew.tax_liability.service`). No com.example default package.