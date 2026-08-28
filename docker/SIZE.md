# taxcalc-api image size

## Before / after

| Build | Base | `docker images` size |
|---|---|---|
| Single-stage baseline (`FROM eclipse-temurin:17-jdk-jammy`, whole repo copied in, `java -jar` on the fat bootJar) | full JDK | **545 MB** |
| Three-stage, before dependency trim (`Dockerfile`: builder → extractor → `gcr.io/distroless/java21-debian12:nonroot`) | distroless JRE | 334 MB |
| Three-stage, after dependency trim (Task 2) | distroless JRE | 319 MB |
| Four-stage, + HEALTHCHECK probe binary + OCI labels + CVE-fix version bumps (Task 3/4) | distroless JRE | 325 MB |
| Six-stage, + jlink custom JRE instead of the full distroless JRE (see below) | distroless java-base + custom JRE | **232 MB** |

Reduction vs. the single-stage baseline: **313 MB / 57.4%**. **Under the
250MB target** — the first build in this project to get there. The +6MB from
319MB → 325MB (Task 3/4) was the static Go HEALTHCHECK probe binary plus
minor deltas from the Tomcat/Netty/Jackson/postgres CVE-fix version bumps;
the -93MB from 325MB → 232MB is the jlink change below.

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

## Against the 250 MB curriculum target — closed via a custom JRE (2026-08-28)

After the dependency trim above, the image was **319 MB** (325MB once Task
3/4 added the HEALTHCHECK binary and CVE fixes), still above the 250 MB
target, for a structural reason: `docker history` showed **166 MB was the
distroless base image itself** —
`gcr.io/distroless/java21-debian12:nonroot`'s bundled, full, pre-built JRE,
containing every JDK module whether this app uses it or not. That's not
something a Dockerfile controls by picking a different tag - `nonroot` was
already the leanest tag in that image family. Investigated three dependency-
level trims to close the remaining gap (see the follow-up sub-section below,
kept for the record) and concluded none of them get close enough - best case
~309MB, still 59MB over.

**What actually closed the gap: `jdeps` + `jlink` build a custom, minimal JRE
instead of shipping the full one.** `jdeps` statically analyzes the app's own
classes plus every dependency jar in `BOOT-INF/lib/` to determine exactly
which JDK modules are referenced; `jlink --add-modules <that list>` packages
only those into a runtime. Paired with switching the base image from
`gcr.io/distroless/java21-debian12` (which bundles a JRE) to
`gcr.io/distroless/java-base-debian12` (glibc + CA certs + the nonroot user
setup, no JRE at all - 44MB vs. ~200MB), the Java-runtime footprint drops from
166MB to 64.5MB actually measured in the image (62MB in isolated testing,
slightly less compression at that stage than the final build). New `docker
history`:

```
117 MB   COPY /extract/dependencies/ ./   (unchanged - this service's production runtime dependencies)
64.5 MB  COPY /customjre /opt/java        (the custom jlink JRE - was 166 MB as the full distroless JRE)
23.4 MB  bookworm//libc6                  (glibc, from java-base-debian12's own base layers)
 ~13 MB  fontconfig/freetype/harfbuzz/libglib2.0/tzdata/etc. (java-base-debian12's own remaining layers)
5.3 MB   COPY /healthcheck ./healthcheck  (unchanged - the Go HEALTHCHECK probe)
 <1 MB   spring-boot-loader + snapshot-dependencies + application layers combined
```

**Total: 232MB.** Under the 250MB target for the first time.

**The real risk with this approach, and how it was actually caught, not just
assumed away**: `jdeps`'s static bytecode analysis cannot see modules only
reached through Java's crypto Service Provider mechanism (TLS cipher suites,
JCE providers) - those are loaded reflectively by the JVM itself, never
called directly in application bytecode, so no amount of static analysis
finds them. This was verified empirically, not just reasoned about: built a
throwaway `jlink` runtime from `jdeps`'s raw output alone and pointed a
minimal `java.net.http.HttpClient` test program at a real external HTTPS
endpoint - it failed **every single time** with
`SSLHandshakeException: handshake_failure` (no `jdk.crypto.ec` means no
ECDHE, which almost every modern TLS server requires). Left uncaught, this
would have silently broken the OAuth2 JWK Set fetch and the Spring AI
Anthropic client the first time either was actually exercised in production,
not at boot - readiness would report healthy right up until the first real
HTTPS call. Added `jdk.crypto.ec`, `jdk.crypto.cryptoki`, `java.naming`,
`java.logging`, and `java.xml` on top of `jdeps`'s own output specifically to
close this gap, then re-ran the same HTTPS test against the augmented module
list and got a clean `200` response before trusting it in the real
Dockerfile. The `RUN` step in the `jlink-builder` stage re-derives the
`jdeps` list on every build (so it adapts if dependencies change) and always
unions it with these five hardcoded modules (so a future dependency change
can't silently drop them again).

**Full functional verification of the resulting image** (not just "does it
boot"): ran it against this machine's live Postgres/MongoDB/Kafka/Redis
stack. `Started Application` logged in 9.5s, zero
`NoClassDefFoundError`/`ClassNotFoundException` anywhere in the logs, Flyway
migrated against Postgres successfully, the MongoDB driver connected and
created its client, `/actuator/health/readiness` settled to `healthy`,
`docker exec <container> /home/nonroot/healthcheck` (the HEALTHCHECK probe
itself) exits 0, and `docker top` confirms the process still runs as UID
65532 under the new entrypoint (`/opt/java/bin/java`, not just `java` - there
is no JRE on `PATH` by default on `java-base-debian12`, only what's
explicitly `COPY`'d in). Trivy findings are unchanged (still 23, same
packages - the base swap didn't add or remove any CVE-relevant OS packages
beyond what's already in the dated waiver).

One separate, pre-existing issue surfaced during this testing, **unrelated to
jlink**: the Kafka consumer can't fully rejoin its group in bridge-network
mode (`-p 8080:8080`, not `--network host`) against this machine's local dev
Kafka broker. The initial bootstrap connection succeeds via
`host.docker.internal:9092` (the `docker` Spring profile), but the broker's
own `advertised.listeners` config reports `localhost:9092` for all
subsequent connections - correct for a process on the host or in
`--network host` mode, unreachable from a bridge-networked container. This
is a local dev-infrastructure config gap (the broker's own advertised
address), not a jlink/module issue, and does not block startup or readiness
(Kafka's listener container retries in the background, same resilient
behavior as any transient broker unavailability) - flagged here for the
record, not something this PR's Dockerfile can fix.

### Dependency-level trims investigated first (kept for the record; superseded by the jlink win above)

Re-investigated three dependency-level candidates before finding the jlink
approach, on the 325 MB image (post-Task-3/4 additions):

| Candidate | Size | Verdict |
|---|---|---|
| `io.rest-assured:json-path` (→ `groovy`) | 7.4 MB | **Not safe to exclude**, confirmed with `./gradlew dependencyInsight`: `spring-ai-anthropic` declares it as a *direct* runtime dependency in its own POM, not an incidental transitive - almost certainly used internally to parse structured JSON output, which is exactly what `summarizeTaxpayer` (W3 D4) exercises. Excluding it risks a silent runtime failure the next time that endpoint is called, not just a startup crash - and confirming it's safe would need a real functional test against the Anthropic API or a WireMock stub, more test infrastructure than this pass has. |
| `swagger-ui-5.17.14.jar` | 3.0 MB | **Freely removable, but it's a feature removal, not a packaging trim.** Dropping it means losing the browsable `/swagger-ui.html` interactive API docs page (deliberately built in W3 D2), keeping only the raw `/v3/api-docs` JSON spec. A legitimate choice some teams make for production images, but a scope decision, not a Dockerfile fix - left in. |
| `docker/healthcheck` static binary | 5.3 MB | Could shrink with a hand-rolled C implementation (raw-socket HTTP GET) instead of Go's stdlib `net/http`, whose runtime carries real fixed overhead. Not attempted: meaningfully more implementation risk (manual HTTP/1.1 response parsing) for one isolated ~5 MB win, and moot now that jlink alone gets under target. |

Best case across all three combined was ~309MB even before jlink — still 59MB
over target at the time. Recorded here to show the work, but the actual fix
was the JRE, not the dependencies: 117MB of production `runtimeClasspath` for
six real integrations was never the problem on its own; 166MB of unused JDK
modules bundled unconditionally into every distroless `java21-debian12`
image was.

## Verification performed

- `docker images uptimecrew/taxcalc-api` → **`232MB`, under the 250MB
  target** (was 319MB pre-Task-3/4, 325MB post-Task-3/4, before the jlink
  change above).
- `docker history --no-trunc uptimecrew/taxcalc-api:0.1.0`:
  - Largest layer is `dependencies/` (117 MB), not `application/` (229 kB) —
    the layered-JAR split is working as intended.
  - No layer is a full JDK or the old full distroless JRE: the Java runtime
    layer is now `COPY /customjre /opt/java` at 64.5MB, the jlink-built
    custom runtime, not `eclipse-temurin:*-jdk-*` and not the ~166MB
    `gcr.io/distroless/java21-debian12` bundled JRE.
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
    comfortably inside it. Unlike the 250MB image-size target (since fixed by
    the jlink change above), this one is a build-time cost, not a shipped
    artifact, so there was less room to trim it further without touching the
    dependency graph itself.
- Full container smoke test against this machine's already-running Postgres /
  MongoDB / Kafka / Redis: `Started Application` logged, Kafka consumer
  joined its group, `curl http://localhost:8080/actuator/health/readiness` →
  `{"status":"UP"}`, zero classloading errors in the logs after the
  dependency trim.
