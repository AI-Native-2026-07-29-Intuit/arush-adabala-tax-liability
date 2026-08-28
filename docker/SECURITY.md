# taxcalc-api docker/SECURITY.md

## Known deviations from the lesson spec

- **No `taxcalc-api/` subdirectory.** This repo predates that convention:
  `build.gradle`, `settings.gradle`, `gradlew`, `gradle/`, and `src/` already
  live at the repo root as a single-module Gradle project (`rootProject.name
  = 'tax_liability'`), not a multi-module project with a `taxcalc-api`
  submodule. `Dockerfile`, `.dockerignore`, and `.hadolint.yaml` are at repo
  root to match, since Docker's build context can't reach outside itself -
  a `Dockerfile` alone in a `taxcalc-api/` folder couldn't `COPY` source that
  lives elsewhere. Fixing this literally would mean moving the entire Gradle
  project into a new subdirectory, a repo-wide restructuring evaluated and
  deliberately declined for this PR (high blast radius, touches most of the
  file tree, unrelated to Docker packaging correctness) rather than
  attempted silently.
- **Builder base is `eclipse-temurin:17-jdk-jammy`, not `:21-jdk-jammy`** -
  see the table below.
- **Image is 325MB, not under the 250MB target** - see `docker/SIZE.md`'s
  "Against the 250MB curriculum target" section for the full breakdown and
  the two rounds of dependency trimming already done (545MB single-stage
  baseline -> 325MB final).

## Base image choices

| Stage | Base image | Why |
|---|---|---|
| `healthcheck-builder` | `golang:1.25-alpine` | Compiles the static HEALTHCHECK probe (see below). Discarded; contributes 0 bytes to the shipped image. |
| `builder` | `eclipse-temurin:17-jdk-jammy` | Full JDK + Gradle. **JDK 17, not 21**: `build.gradle` pins `java.toolchain.languageVersion=17` with no toolchain auto-provisioning configured, so the builder's JDK major version must match exactly. Discarded after `bootJar`. |
| `extractor` | `eclipse-temurin:21-jre-jammy` | Runs `layertools extract` on the boot JAR. JRE only, no Gradle. The Java-17-compiled class files run unmodified on a Java 21 JRE. Discarded after extraction. |
| `debug` (opt-in only) | `gcr.io/distroless/java21-debian12:debug-nonroot` | Same app layers as `runtime`, but the base bundles BusyBox (shell + `id`/`ls`/`cat`/...) - see "Debug build target" below. Only built with `docker build --target debug`; never the default, never pushed. |
| `runtime` (shipped, final) | `gcr.io/distroless/java21-debian12:nonroot` | No shell, no package manager, no user-management tools — the smallest attack surface available for a Java 21 workload. The last stage in the file, so this is what `docker build .` (no `--target`) produces, and the only stage ever pushed. |

All five base images are pinned by digest (`@sha256:...`), not by tag. A tag is
mutable; the same `:nonroot` reference can point at different bytes on
different days. A digest is content-addressed and immutable.

## Debug build target

`gcr.io/distroless/java21-debian12:nonroot` ships zero shell/coreutils by
design - there is no `id`, `sh`, `ls`, or anything else for `docker exec` to
run, so `docker exec <container> id` fails outright
(`exec: "id": executable file not found in $PATH`). That is the entire point
of distroless: no shell means an attacker who gets code execution inside the
container still can't do anything shell-shaped with it. `docker top` and
`docker inspect` prove the same fact (the process runs as UID 65532, not
root) without needing a shell at all, and are the correct tools for verifying
a properly hardened container - see the PR for the literal command's output.

For the rare case where a human genuinely needs to `exec` in (e.g. manually
confirming `id`'s exact `uid=65532(nonroot) gid=65532(nonroot)` output), the
Dockerfile has an opt-in `debug` stage: identical app layers, but based on
`gcr.io/distroless/java21-debian12:debug-nonroot`, which bundles BusyBox.
It is **never** the default build target - `runtime` is the last stage in the
file, which is what `docker build .` produces with no `--target` flag, so
every existing build command (Task 1's, CI's) is completely unaffected - and
it is never pushed to any registry; only `runtime` is. Build it explicitly:

```bash
docker build --target debug -t uptimecrew/taxcalc-api:debug .
docker run -d --name taxcalc-api-debug uptimecrew/taxcalc-api:debug
docker exec taxcalc-api-debug id
# uid=65532(nonroot) gid=65532(nonroot) groups=65532(nonroot)
```

This is a real trade-off, not a free win: the `debug` image is 523MB (vs.
325MB for `runtime`) specifically because it has a shell and utilities the
shipped image deliberately doesn't. Treat any image built from the `debug`
stage as a local development tool only, never as something to run in any
shared or production environment.

## Pinned digests (current)

```text
golang:1.25-alpine                                @sha256:1ae0735f00daffa3aaf1363a5184c0d2dc55c78e3db4ec70241cdac97bf84b59
eclipse-temurin:17-jdk-jammy                      @sha256:400014962ad7224461f945bb1cc3d7d5a1927ce15b8245b72d9cedcda554cd2a
eclipse-temurin:21-jre-jammy                      @sha256:eebd356ad7358b7094758e5787a6726f332917cfd56feab6457c56dab895cdbf
gcr.io/distroless/java21-debian12:debug-nonroot   @sha256:9be3a4d32b386cb2970368e6c605d8dd47f0242660ea732b66e7c5c099b03955   # debug stage only, never shipped
gcr.io/distroless/java21-debian12:nonroot         @sha256:7e37784d94dccbf5ccb195c73b295f5ad00cd266512dfbac12eb9c3c28f8077d   # runtime stage, shipped
```

Refresh the digests on the **first business day of each month**, or
immediately if Trivy reports a newly-discovered HIGH/CRITICAL on the current
digest. Bump the `APP_VERSION` build-arg on every refresh.

## HEALTHCHECK

Distroless ships no shell, `curl`, or `wget`, so `HEALTHCHECK` can't run an
inline command. Instead, `docker/healthcheck/main.go` is a ~2MB static Go
binary (no CGO, stripped) that GETs `/actuator/health/readiness` and exits
0/1, compiled by the dedicated `healthcheck-builder` stage and copied into the
final image at `/home/nonroot/healthcheck`. `/actuator/health/readiness` and
`/actuator/health/liveness` are exposed via `management.endpoint.health.probes.enabled=true`
in `application.yml` (Spring Boot only auto-enables them when it detects a
Kubernetes environment; a plain `docker run` needs the property set
explicitly), and permitted without authentication via
`.requestMatchers("/actuator/health/**").permitAll()` in `SecurityConfig.java`
— both of these were bugs found and fixed while wiring up this HEALTHCHECK
(see git history on this PR): the security matcher previously permitted only
the exact `/actuator/health` path, 401-ing the `/readiness` and `/liveness`
sub-paths Docker/Kubernetes probes actually hit.

## Three commands

```bash
# 1. Build (with version + git sha for OCI labels).
docker build \
  --build-arg APP_VERSION=0.1.0 \
  --build-arg GIT_SHA=$(git rev-parse --short HEAD) \
  -t uptimecrew/taxcalc-api:0.1.0 .

# 2. Scan (fail-by-default on HIGH/CRITICAL with a fix available).
trivy image --severity HIGH,CRITICAL --ignore-unfixed uptimecrew/taxcalc-api:0.1.0

# 3. Run (non-root inside, host port 8080, resource caps applied).
docker run -d --name taxcalc-api \
  --memory=512m --cpus=1.0 \
  -p 8080:8080 \
  uptimecrew/taxcalc-api:0.1.0
```

## Registry push (verified 2026-08-27, last updated same day after adding the opt-in debug build target)

Pushed to GitHub Container Registry (GHCR), private visibility, under the
author's personal namespace (no AWS ECR credentials available in this
environment - see AWS path in the Prerequisites). This is always the
`runtime` stage (distroless `:nonroot`, no shell) - the `debug` stage above
is never pushed anywhere:

```
ghcr.io/arushadabala/taxcalc-api:0.1.0
ghcr.io/arushadabala/taxcalc-api:8ac9709   # <git-sha>, same digest
```

Both tags resolve to the same immutable digest:

```
ghcr.io/arushadabala/taxcalc-api@sha256:a89211fb8e01082d80d0a2ae003cf5eeaf40488fd8967bf98814af599178bc6b
```

The `0.1.0` tag was overwritten once, in place, during this PR's review
(originally pushed at commit `7499789`, before the `docker`-profile readiness
fix). That is a one-time exception for pre-merge iteration on an
unpublished/unconsumed artifact, not a rejection of the "immutable tags"
policy below - after merge, a fix like this would ship as `0.1.1`, not a
second push to the same `0.1.0` tag.

`.github/workflows/docker.yml`'s `build-scan-smoke` job intentionally does
**not** push (PR builds use `load: true` only). The main-branch push path
(ECR via OIDC, or this same GHCR fallback) is documented here but not wired
into the workflow - out of scope for this deliverable.

## Scan cadence

- **On every PR**: `.github/workflows/docker.yml` runs Trivy with
  `--severity HIGH,CRITICAL --ignore-unfixed --exit-code 1`. A new finding
  fails the PR.
- **On merge to `main`**: the same scan, plus push to the private registry
  (out of scope for this deliverable - see below).
- **Weekly cron** (not implemented in this PR): re-scan the currently-deployed
  digest. New CVEs land daily on unchanged bytes, so an image that was clean
  on Monday can be dirty by Friday.

## Tagging policy

- `uptimecrew/taxcalc-api:0.1.0` — human-friendly semver, immutable.
- `uptimecrew/taxcalc-api:<git-sha>` — exact source pin, immutable.
- Never `uptimecrew/taxcalc-api:latest`. Mutable references are forbidden in
  prod manifests (Compose, Kubernetes, ECS). Deploys reference the digest:
  `uptimecrew/taxcalc-api@sha256:...`.

## Trivy scan waiver — 2026-08-27

`trivy image --severity HIGH,CRITICAL --ignore-unfixed uptimecrew/taxcalc-api:0.1.0`
reports **23 findings** (1 OS package, 22 Java dependencies) as of this date.
Before waiving anything, three fixes were verified safe and applied (see
`build.gradle`):

| Fix | From → To | Findings closed |
|---|---|---|
| `ext['tomcat.version']` override | 10.1.42 → 10.1.55 | 14 (incl. 3 CRITICAL) |
| `ext['netty.version']` override | 4.1.122.Final → 4.1.136.Final | 18 |
| `ext['jackson-bom.version']` override | 2.17.3 → 2.18.8 | 3 |
| `golang:1.22-alpine` → `golang:1.25-alpine` (healthcheck-builder) | Go 1.22 → 1.25.14 | 22 (all findings in the healthcheck binary) |
| `org.postgresql:postgresql` explicit version | 42.7.3 → 42.7.11 | 1 (a newer CVE, fixed only in 42.7.12, has since appeared — see table below) |

One further fix was **attempted and reverted**: bumping
`org.springframework.ai:spring-ai-starter-*` from 1.0.0 → 1.0.7 (which would
have closed 3 findings) crashed the app at startup —
`NoClassDefFoundError: com/networknt/schema/dialect/Dialects` while
constructing `mcpSyncServer` — because 1.0.7's MCP server autoconfiguration
needs a newer `com.networknt:json-schema-validator` than what resolves on
this project's classpath. Verified by rebuilding the image and running it
against this machine's live Postgres/Mongo/Kafka/Redis stack: 1.0.0 boots
cleanly, 1.0.7 does not. Reverted rather than shipping a broken image to hit
a lower CVE count.

The remaining 23 are waived as of **2026-08-27**, re-evaluate by
**2026-09-27** (or sooner, on the next scheduled digest/dependency refresh):

| Package | Installed | Fix needs | Why not fixed now |
|---|---|---|---|
| `liblcms2-2` (OS, debian 12.13) | 2.14-2 | 2.14-2+deb12u1 | Base image's OS package, not this project's dependency graph. Not present in the `gcr.io/distroless/java21-debian12:nonroot` digest pinned above; will close automatically on the next monthly digest refresh once upstream rebuilds. |
| `commons-fileupload`, `commons-io`, `micrometer-core`, `kafka-clients`, `lz4-java`, `postgresql` (42.7.12) | various | patch releases | Boot-BOM-managed or independently pinned; each needs its own `ext[]`/version override the same way Tomcat/Netty/Jackson did above, not yet done for these six - lower severity, deferred to the next pass rather than rushed in this PR. |
| `spring-ai-client-chat`, `spring-ai-model` | 1.0.0 | 1.0.7+ | See revert above - the fix requires the same spring-ai bump that crashed the MCP server. Needs a coordinated fix (likely also bumping/pinning `com.networknt:json-schema-validator` explicitly) before it can be taken. |
| `spring-boot`, `spring-core`, `spring-expression`, `spring-webflux`, `spring-webmvc`, `spring-security-web`, `spring-data-commons`, `spring-data-mongodb`, `spring-kafka`, `spring-graphql` | 3.3.13 / 6.1.21 / 6.3.10 / etc. | a Spring Boot **minor or major** version bump (3.3 → 3.5+/4.0, Spring Framework 6.1 → 6.2/7.0, Spring Security 6.3 → 6.5/7.0) | None of these have a same-line patch fix; every one requires moving to a different minor/major release. That's a coordinated, whole-project upgrade with real compatibility risk across the entire codebase (unlike the isolated Tomcat/Netty/Jackson `ext[]` overrides, which stay within their already-qualified line) - out of scope for this Docker packaging deliverable. Tracked as follow-up work, not silently accepted risk. |

This waiver is scoped to image tag `0.1.0` / digest as built on 2026-08-27. A
rebuild that changes any of the versions above must re-run the scan and
update this table, not assume the waiver still applies.
