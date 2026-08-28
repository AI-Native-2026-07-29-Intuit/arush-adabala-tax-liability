# Live-reload dev loop

Two terminals, both from the repo root. Prereqs: `docker compose up -d --wait`
already ran once so `postgres`/`redis`/`kafka`/`mongo` are healthy.

**Terminal 1 (host)** - recompile classes on every save (deliberately not
`bootJar`: this loop needs `build/classes/java/main` to change, not a
repackaged fat jar):

    ./gradlew classes --continuous

**Terminal 2 (host)** - bring up the dev-profile container, which bind-mounts
the repo root at `/workspace` and runs `./gradlew bootRun` with DevTools'
restart classloader + JDWP on 5005:

    docker compose --profile dev up -d taxcalc-api-dev
    docker compose --profile dev logs -f taxcalc-api-dev

Edit any file under `src/main/java` and save. Terminal 1 finishes recompiling
in about a second; DevTools' 1s poll interval picks up the changed `.class`
file and restarts the app context - terminal 2's log prints `Restarting due
to N class path change...` within 3s of the save (empirically ~2.6s),
followed by a full context restart (a few more seconds, since Flyway/Kafka
consumer group join, etc. still run for real on every restart) - no
container restart needed either way.

## Why `bootRun`, not `java -jar`

`spring-boot-devtools` is a `developmentOnly` Gradle dependency (see
`build.gradle`) - the Spring Boot Gradle plugin deliberately strips
`developmentOnly` deps out of the packaged `bootJar`, so a container running
`java -jar build/libs/tax_liability-*.jar` never even loads devtools (no
"Devtools property defaults active!" banner, no restart classloader). It
only ends up on the classpath for `bootRun` / IDE-driven runs, which is why
`taxcalc-api-dev` runs `./gradlew bootRun` inside the container instead.

That also means `taxcalc-api-dev` needs a JDK, not just a JRE - it's on
`eclipse-temurin:17-jdk-jammy` (17 to match the project's Gradle toolchain in
`build.gradle`, not 21). The `gradle-cache` named volume persists the
Gradle wrapper distribution + resolved dependencies across container
restarts so only the very first `docker compose --profile dev up` pays that
download cost.

## Ports

- `http://localhost:8081` - taxcalc-api-dev's app port (the prod-shaped
  `taxcalc-api` service keeps 8080 for itself)
- `localhost:5006` - JDWP debugger (attach IntelliJ's Remote JVM Debug here)

Note: the prod-shaped `taxcalc-api` service does NOT support `-agentlib:jdwp`
- its Day 1 custom jlink JRE was trimmed to only the modules `jdeps` found
referenced and doesn't include `jdk.jdwp.agent`. `taxcalc-api-dev` uses a
full JDK specifically so debug + live-reload work without touching that
hardened runtime.

Tear down with `docker compose --profile dev down`.
