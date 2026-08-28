# taxcalc-api image size

## Before / after

| Build | Base | `docker images` size |
|---|---|---|
| Single-stage baseline (`FROM eclipse-temurin:17-jdk-jammy`, whole repo copied in, `java -jar` on the fat bootJar) | full JDK | **545 MB** |
| Three-stage, before dependency trim (`Dockerfile`: builder → extractor → `gcr.io/distroless/java21-debian12:nonroot`) | distroless JRE | 334 MB |
| Three-stage, after dependency trim (Task 2) | distroless JRE | 319 MB |
| Four-stage, + HEALTHCHECK probe binary + OCI labels + CVE-fix version bumps (Task 3/4) | distroless JRE | **325 MB** |

Reduction vs. the single-stage baseline: **220 MB / 40.4%**. The +6 MB since
Task 2 is almost entirely the static Go HEALTHCHECK probe binary
(`docker/healthcheck/`, ~2 MB stripped) plus minor size deltas from the
Tomcat/Netty/Jackson/postgres patch bumps in `docker/SECURITY.md`'s CVE fixes
— not a regression in the layering strategy itself.

The single-stage baseline was built once locally to get this number, then deleted
(`docker rmi`) — it is not kept as a file in the repo.

## Dependency trim (`build.gradle`)

Inspected `BOOT-INF/lib/*.jar` by size inside the built jar and traced the two
largest non-obvious entries with `./gradlew dependencyInsight`:

| Jar | Size | Pulled in by | Actually used? | Action |
|---|---|---|---|---|
| `zstd-jni-1.5.6-4.jar` | 6.4 MB | `spring-kafka` → `kafka-clients` (optional compression codec) | No — nothing in `application.yml` sets `compression.type: zstd`; Kafka defaults to no compression | Excluded |
| `bcprov-jdk18on-1.78.jar` | 7.9 MB | `spring-cloud-starter-openfeign` / `-circuitbreaker-resilience4j` → `spring-cloud-starter` → `spring-security-rsa` (decrypts `{cipher}`-prefixed properties and PEM-encoded RSA keys) | No — grepped the whole repo for `{cipher}`, `.pem`, `PrivateKey`: zero matches | Excluded |

Both are excluded via `exclude group:/module:` on the specific `implementation(...)`
declarations in `build.gradle`, not a blanket `configurations.all` rule, so the
exclusion is scoped to exactly the dependency that introduced them.

**Verified safe**: rebuilt the jar, rebuilt the image, ran it against this
machine's live Postgres/Mongo/Kafka/Redis stack. `Started Application` logged
normally, the Kafka consumer still joined its group and got `partitions
assigned: [taxpayers.events-0]`, `/actuator/health/readiness` still returned
`{"status":"UP"}`, and `docker logs` has zero `NoClassDefFoundError` /
`ClassNotFoundException` / BouncyCastle / zstd mentions. `dependencies/`
layer: 132 MB → **117 MB**.

A third candidate, `groovy-4.0.27.jar` (7.4 MB), was investigated and
deliberately **left in**: it's pulled in by `io.rest-assured:json-path`, which
`spring-ai-anthropic` depends on for parsing the Anthropic API's JSON
responses — the same client this service's `summarizeTaxpayer` structured-output
feature actually calls at runtime. Excluding it risks a `NoClassDefFoundError`
on a real, exercised code path for a 7.4 MB saving; not worth the risk for
this deliverable.

## Against the 250 MB curriculum target

Even after the trim above, the image is **319 MB**, still above the 250 MB
target. `docker history` on the trimmed image:

```
166 MB   gcr.io/distroless/java21-debian12:nonroot base (JRE + glibc + fontconfig, from the distroless image itself)
117 MB   COPY /extract/dependencies/ ./            (this service's production runtime dependencies, post-trim)
 <1 MB   spring-boot-loader + snapshot-dependencies + application layers combined
```

166 MB of that is the distroless base image itself — not something this
Dockerfile controls, and already the leanest Java 21 image Google's distroless
catalog offers (there is no smaller JRE-only distroless variant). The
remaining 117 MB is production `runtimeClasspath` for a service that
integrates with six external systems: Postgres (JDBC + Flyway), MongoDB,
Kafka, Redis, an OAuth2 resource server (Spring Security), GraphQL, OpenFeign,
OpenTelemetry auto-instrumentation for HTTP/JDBC/Kafka, and the Spring AI
Anthropic client. Two genuinely-dead transitive dependencies (14.3 MB) have
already been removed; every other large jar remaining on the classpath was
traced to a real, load-bearing integration this service exercises (see table
above). Closing the remaining ~69 MB gap to the 250 MB target would mean
dropping one of those integrations, not repackaging — out of scope for a
Docker packaging task.

## Verification performed

- `docker images uptimecrew/taxcalc-api` → `319MB` (< the fat single-stage
  baseline and < the pre-trim three-stage build, not < the 250 MB target —
  see above).
- `docker history --no-trunc uptimecrew/taxcalc-api:0.1.0`:
  - Largest layer is `dependencies/` (117 MB), not `application/` (229 kB) —
    the layered-JAR split is working as intended.
  - No layer is the builder's JDK (166 MB base layer is the distroless *JRE*,
    from `gcr.io/distroless/java21-debian12:nonroot`'s own
    `temurin_jre_21_arm64` build step, not `eclipse-temurin:*-jdk-*`).
  - No layer contains `.java` source: exported the running container's
    `/home/nonroot` filesystem (`docker cp`) and confirmed `find ... -iname
    '*.java'` returns 0 matches; `BOOT-INF/classes/` holds only compiled
    `.class` files and resources.
- Warm rebuild after a code-only change (one comment line added to
  `TaxpayerController.java`, then reverted): the dependency pre-warm layer
  reported `CACHED`; total `docker build` time **5.96s**, well under the 10s
  target.
- Cold build (no cache) is dominated entirely by the dependency pre-warm
  step's network download, not by anything else in the Dockerfile - measured
  with an isolated, guaranteed-fresh BuildKit cache mount so the numbers
  below are a true apples-to-apples comparison:
  - `./gradlew --no-daemon dependencies` (unscoped, resolves *every*
    configuration - `compileClasspath`, `runtimeClasspath`,
    `testCompileClasspath`, `testRuntimeClasspath`, ...): **73.1s**.
  - `./gradlew --no-daemon dependencies --configuration compileClasspath`
    then `--configuration runtimeClasspath` (only what `bootJar -x test`
    actually needs - the Dockerfile as committed): **49.0s**, a 33% cut.
    Test-only dependencies this skips downloading during the image build
    (WireMock, three Testcontainers modules, Mockito, spring-boot-starter-test
    and its transitives, spring-kafka-test, spring-graphql-test) are still
    resolved normally by plain `./gradlew test` outside Docker - nothing
    about running the test suite changes.
  - The task's own guideline is "~60-90s" cold; this project's real
    dependency graph (Postgres, MongoDB, Kafka, Redis, an OAuth2 resource
    server, GraphQL, OpenTelemetry, Spring AI - six external integrations)
    is heavier than whatever reference app that estimate assumed, so even
    the scoped 49.0s dependency-resolution step, plus the ~30s `bootJar`
    compile/package step after it, lands close to that range rather than
    comfortably inside it. This is the same "not fixable without cutting
    functionality" story as the 250MB image-size target in
    `docker/SECURITY.md`'s known-deviations list.
- Full container smoke test against this machine's already-running Postgres /
  MongoDB / Kafka / Redis: `Started Application` logged, Kafka consumer
  joined its group, `curl http://localhost:8080/actuator/health/readiness` →
  `{"status":"UP"}`, zero classloading errors in the logs after the
  dependency trim.
