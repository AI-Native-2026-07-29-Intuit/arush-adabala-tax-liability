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
- **Image is now 232MB, under the 250MB target**, via a custom `jlink`-built
  JRE instead of shipping the full distroless one - see "Custom JRE via
  jlink" below and `docker/SIZE.md` for the full investigation (545MB
  single-stage baseline -> 325MB via dependency trimming -> 232MB via jlink).

## Base image choices

| Stage | Base image | Why |
|---|---|---|
| `healthcheck-builder` | `golang:1.25-alpine` | Compiles the static HEALTHCHECK probe (see below). Discarded; contributes 0 bytes to the shipped image. |
| `builder` | `eclipse-temurin:17-jdk-jammy` | Full JDK + Gradle. **JDK 17, not 21**: `build.gradle` pins `java.toolchain.languageVersion=17` with no toolchain auto-provisioning configured, so the builder's JDK major version must match exactly. Discarded after `bootJar`. |
| `extractor` | `eclipse-temurin:21-jre-jammy` | Runs `layertools extract` on the boot JAR. JRE only, no Gradle. The Java-17-compiled class files run unmodified on a Java 21 JRE. Discarded after extraction. |
| `jlink-builder` | `eclipse-temurin:21-jdk-jammy` | Runs `jdeps` + `jlink` to build a custom, minimal JRE - see "Custom JRE via jlink" below. Needs a full JDK's `jmods`, not just a JRE. Discarded after the custom runtime is copied out. |
| `debug` (opt-in only) | `gcr.io/distroless/java-base-debian12:debug-nonroot` | Same app layers + custom JRE as `runtime`, but the base bundles BusyBox (shell + `id`/`ls`/`cat`/...) - see "Debug build target" below. Only built with `docker build --target debug`; never the default, never pushed. |
| `runtime` (shipped, final) | `gcr.io/distroless/java-base-debian12:nonroot` | No shell, no package manager, no user-management tools, and (unlike `java21-debian12`) no bundled JRE either — just glibc + CA certs + the nonroot user setup. The custom jlink runtime is the only Java runtime in the image. The last stage in the file, so this is what `docker build .` (no `--target`) produces, and the only stage ever pushed. |

All six base images are pinned by digest (`@sha256:...`), not by tag. A tag is
mutable; the same `:nonroot` reference can point at different bytes on
different days. A digest is content-addressed and immutable.

## Custom JRE via jlink

`gcr.io/distroless/java21-debian12` bundles a full, pre-built OpenJDK JRE -
every module, whether this app uses it or not (~166MB just for that). The
`jlink-builder` stage builds a custom, minimal runtime instead:

1. `jdeps -q --multi-release 21 --ignore-missing-deps --print-module-deps --recursive --class-path 'deps/*' classes` statically analyzes the app's own compiled classes plus every dependency jar in `BOOT-INF/lib/` to find which JDK modules are actually referenced.
2. `jlink --add-modules <that list + five hardcoded modules, see below>` packages only those modules into a runtime, copied into the final image at `/opt/java` (so `ENTRYPOINT` uses the full path `/opt/java/bin/java` - there's no JRE on `PATH` by default on `java-base-debian12`, only what's explicitly `COPY`'d in).

Measured result: **64.5MB**, versus 166MB for the full distroless JRE.

### What this change broke, and the fix - found by testing, not assumed

`jdeps` cannot see modules only reached through Java's crypto Service
Provider mechanism (TLS cipher suites, JCE providers) - those are loaded
reflectively by the JVM itself at the TLS-engine layer, never called
directly in application bytecode, so no amount of static analysis finds
them. This was verified empirically, in three steps, before trusting it in
the real Dockerfile:

1. **Broke**: built a throwaway `jlink` runtime from `jdeps`'s raw,
   unmodified output and pointed a minimal `java.net.http.HttpClient` test
   program at a real external HTTPS endpoint (`api.github.com`). Every run
   failed with `SSLHandshakeException: handshake_failure` - `jdk.crypto.ec`
   was missing, meaning no ECDHE cipher support, which almost every modern
   TLS server requires for the handshake at all. Undetected, this would have
   silently broken the OAuth2 JWK Set fetch and the Spring AI Anthropic
   client the first time either was actually exercised in production, not
   at boot - readiness would report healthy right up until the first real
   HTTPS call.
   **Fix**: added `jdk.crypto.ec` and `jdk.crypto.cryptoki` to
   `--add-modules`. Re-ran the same test - the handshake failure was gone.
2. **Still failed differently after the fix above**: the same test then hit
   `PKIX path building failed: unable to find valid certification path`.
   Investigated rather than assumed to be a second module gap: ran the
   identical test against the *stock, untrimmed* JDK (no jlink involved at
   all) and got the exact same PKIX error - proving this was this
   development machine's pre-existing corporate TLS-inspection proxy
   (Zscaler, the same one behind every `gradlew` cold-download workaround
   used elsewhere in this repo's local verification), not anything jlink
   removed. Confirmed conclusively by importing the corporate root CA into
   the jlink runtime's own `cacerts` and re-running the test: clean
   `200 OK`. That CA-import step is local-machine-only, not part of the
   committed Dockerfile - it only existed to get an unambiguous, fully-clean
   success signal on this one network.
3. **Added defensively, not from a caught failure**: `java.naming` (JNDI -
   commonly needed by JDBC drivers and logging frameworks even without an
   obvious direct call), `java.logging` (`java.util.logging`, which several
   libraries bridge to/from even when SLF4J is the primary API), and
   `java.xml` (XML processing several Spring/Jackson internals touch).
   These weren't proven necessary by a specific reproduced failure the way
   `jdk.crypto.ec` was - added as a documented safety margin given
   `jdeps`'s known blind spot for reflection/SPI-loaded code, rather than
   risk finding a third gap later, in production, on a path this session's
   testing didn't happen to exercise.

The final module list is `jdeps`'s own output - re-derived on every build,
so it adapts automatically if dependencies change - unioned with these five
hardcoded modules, so a future dependency change can't silently drop them
again.

**Full functional verification performed on the resulting image**, not just
"does it boot": ran against this machine's live Postgres/MongoDB/Kafka/Redis
stack. `Started Application` in 9.5s, zero
`NoClassDefFoundError`/`ClassNotFoundException` anywhere in the logs, Flyway
migrated against Postgres successfully, MongoDB driver connected,
`/actuator/health/readiness` settled to `healthy`, the HEALTHCHECK probe
itself exits 0, and `docker top` confirms the process still runs as UID
65532. Trivy findings are unchanged (still 23, same waiver applies - the
base swap didn't add or remove any CVE-relevant OS packages). Full
investigation and numbers in `docker/SIZE.md`.

One pre-existing, unrelated issue surfaced during this testing: the Kafka
consumer can't fully rejoin its group in bridge-network mode against this
machine's local dev Kafka broker, because the broker's own
`advertised.listeners` config reports `localhost:9092` for reconnection
(correct for `--network host`, unreachable from bridge mode) - a local
dev-infrastructure config gap, not a jlink issue, and it doesn't block
startup or readiness. See `docker/SIZE.md` for detail.

## Debug build target

`gcr.io/distroless/java-base-debian12:nonroot` ships zero shell/coreutils by
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
Dockerfile has an opt-in `debug` stage: identical app layers + the same
custom jlink JRE as `runtime`, but based on
`gcr.io/distroless/java-base-debian12:debug-nonroot`, which bundles BusyBox.
It is **never** the default build target - `runtime` is the last stage in the
file, which is what `docker build .` produces with no `--target` flag, so
every existing build command (Task 1's, CI's) is completely unaffected - and
it is never pushed to any registry; only `runtime` is. Build and verify it
explicitly (re-verified after the jlink change):

```bash
docker build --target debug -t uptimecrew/taxcalc-api:debug .
docker run -d --name taxcalc-api-debug uptimecrew/taxcalc-api:debug
docker exec taxcalc-api-debug id
# uid=65532(nonroot) gid=65532(nonroot) groups=65532(nonroot)
```

This is a real trade-off, not a free win: the `debug` image is 233MB (vs.
232MB for `runtime`) specifically because it has a shell and utilities the
shipped image deliberately doesn't (roughly break-even now, since both
images use the same 64.5MB custom JRE - before the jlink change, `debug` was
523MB against `runtime`'s 325MB). Treat any image built from the `debug`
stage as a local development tool only, never as something to run in any
shared or production environment.

## Pinned digests (current)

```text
golang:1.25-alpine                                @sha256:1ae0735f00daffa3aaf1363a5184c0d2dc55c78e3db4ec70241cdac97bf84b59
eclipse-temurin:17-jdk-jammy                      @sha256:400014962ad7224461f945bb1cc3d7d5a1927ce15b8245b72d9cedcda554cd2a
eclipse-temurin:21-jre-jammy                      @sha256:eebd356ad7358b7094758e5787a6726f332917cfd56feab6457c56dab895cdbf
eclipse-temurin:21-jdk-jammy                       @sha256:ce5767b7222312d42395f5bab033cd91f09e44032a2f21bdfd7b5b912dbe1e77   # jlink-builder only
gcr.io/distroless/java-base-debian12:debug-nonroot @sha256:dba159ea506850709f6a3b925a90e12a461084145b35266e18d585c433a21f62   # debug stage only, never shipped
gcr.io/distroless/java-base-debian12:nonroot       @sha256:a9930cad62d02853d7f3dede7281c4b916cbf74493c2d8d38564121aad92bf6c   # runtime stage, shipped
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

## Registry push (verified 2026-08-27, last updated 2026-08-28 after the jlink custom-JRE change)

Pushed to GitHub Container Registry (GHCR), private visibility, under the
author's personal namespace (no AWS ECR credentials available in this
environment - see AWS path in the Prerequisites). This is always the
`runtime` stage (distroless `java-base-debian12:nonroot` + the custom jlink
JRE, no shell) - the `debug` stage above is never pushed anywhere:

```
ghcr.io/arushadabala/taxcalc-api:0.1.0
ghcr.io/arushadabala/taxcalc-api:6fa590e   # <git-sha>, same digest
```

Both tags resolve to the same immutable digest:

```
ghcr.io/arushadabala/taxcalc-api@sha256:31e9f09313e2271651fa93e18567461d313a12316a3d10b3a34848c7e5ff810a
```

232MB, under the 250MB target.

(Re-pushed once more after a `git filter-branch` rewrite removed the
`Co-Authored-By` trailer from every commit on this branch - the branch's
commit hashes all changed, so the previously-pushed image's baked-in
`org.opencontainers.image.revision` label pointed at a git SHA that no
longer exists in history. This push has no code changes, only the label.)

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

## Trivy: FIXED, not waived — 2026-09-04 (tomcat-embed-core, 3 CRITICALs)

Three CRITICAL CVEs landed against `tomcat-embed-core` 10.1.55 after the waivers
below were written. They were **fixed by upgrading, not added to `.trivyignore`** —
all three are authentication/authorization bypasses with a drop-in patch release
available, which is not a defensible thing to waive.

| CVE | Severity | Issue |
|---|---|---|
| CVE-2026-65182 | CRITICAL | Security constraint bypass |
| CVE-2026-65905 | CRITICAL | Authentication bypass via limited request |
| CVE-2026-68525 | CRITICAL | Unauthorized access |

**Found by `oidc-ecr-poc.yml`, not by a human re-reading the file.** The gate is
`severity: HIGH,CRITICAL` + `ignore-unfixed: true`, and these are `fixed` upstream,
so `ignore-unfixed` did not hide them and no existing `.trivyignore` line covered
them. The scan went red on a branch that changed nothing about the application —
the vulnerability feed was the only variable, exactly as with the libexpat1
addendum below.

**The fix is 10.1.59, and deliberately not the 10.1.58 the advisories name.**
10.1.58 was never published to Maven Central: the 10.1.x `maven-metadata.xml`
lists `…10.1.56, 10.1.57, 10.1.59`, and a direct fetch of the 10.1.58 directory
returns HTTP 404, so Gradle resolves `ext['tomcat.version'] = '10.1.58'` as
`10.1.55 -> 10.1.58 FAILED`. 10.1.59 supersedes it and carries the same fixes.
Anyone "correcting" the version in `build.gradle` to match the advisory text will
break the build.

10.1.x is within Spring Boot 3.3's supported Tomcat line, so this needed only the
existing `ext['tomcat.version']` override — no Spring Boot minor/major bump, which
is what the remaining waived Spring CVEs below are blocked on.

## Trivy waiver addendum — 2026-09-02 (libexpat1, CVE-2026-56408)

One CVE was published after the 2026-08-27 waiver below and turned the
`build-scan-smoke` gate red on a branch that changed nothing about the image.

**Confirmed environmental, not a regression.** `libexpat1` is an OS package inside
the pinned distroless base and is referenced nowhere in `Dockerfile`,
`build.gradle` or `pom.xml`. Re-running *main's own* last-green docker run
(`33534774002`, commit `f55a156`, green on 2026-09-01) against the following day's
Trivy database reproduced the identical failure — same commit, same code, opposite
result, with the vulnerability feed as the only variable.

**Why a waiver rather than a base-image bump.** The bump is the preferred fix and
was checked first: the advisory names `2.5.0-1+deb12u3` as fixed, but
`gcr.io/distroless/java-base-debian12:nonroot` still resolves to
`sha256:a9930cad62d02853d7f3dede7281c4b916cbf74493c2d8d38564121aad92bf6c` — the
digest already pinned. Google has not published a rebuilt image, so there is no
newer digest to move to.

**Exposure.** libexpat is an XML parser pulled in as a transitive OS dependency of
the java-base image; this service parses JSON and GraphQL, not XML, and exposes no
route that feeds attacker-controlled input to a system XML parser. The integer
overflow in `copyString` needs untrusted XML to reach expat, which this
application does not provide.

| CVE | Package | Installed | Fixed in | Why waived | Re-evaluate |
|---|---|---|---|---|---|
| CVE-2026-56408 | libexpat1 (base image) | 2.5.0-1+deb12u2 | 2.5.0-1+deb12u3 | No rebuilt distroless image exists yet — `:nonroot` still resolves to the pinned digest. No app code path reaches expat. | 2026-09-27 |

On the expiry date, re-scan first: if a newer distroless digest carries deb12u3,
bump the pin in the `Dockerfile` and delete the `.trivyignore` line rather than
renewing it.

## Trivy scan waiver — 2026-08-27

### Why a waiver instead of 0 HIGH / 0 CRITICAL

The task target is 0 unfixed HIGH/CRITICAL, with three explicitly-sanctioned
paths to get there: upgrade the dependency, bump the base-image digest, or
document a dated, scoped waiver. All three were used - the first two closed
57 of the original 80 findings (see table below); a waiver covers the
remaining 23 because closing them for real means one specific thing:
**~14 of the 23 require a Spring Boot *minor or major* version bump**
(3.3 -> 3.5+/4.0, Spring Framework 6.1 -> 6.2/7.0, Spring Security 6.3 ->
6.5/7.0), not a patch. Checked directly against Maven Central
(`spring-boot-gradle-plugin`'s `maven-metadata.xml`): **3.3.13 - what this
project already runs - is the newest release in the entire 3.3.x line.**
There is no smaller, safer bump available for these findings the way there
was for Tomcat/Netty/Jackson (all three stayed within their already-adopted
major.minor line via a targeted `ext[]` override).

A cross-Spring-Boot-generation bump moves roughly a dozen interdependent
library versions at once across the entire codebase - a real, standalone
upgrade decision with its own regression risk and review, not something that
belongs inside a Docker-packaging PR. This isn't a theoretical caution: the
one bump attempted in this pass that looked equally safe on paper
(`spring-ai` 1.0.0 -> 1.0.7, same minor line, would have closed 3 findings)
**broke the app for real** when actually tested - see the revert below. If a
same-minor-line patch bump could do that, treat a cross-generation Spring
Boot bump as materially riskier, not less.

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
| `liblcms2-2` (OS, debian 12.13) | 2.14-2 | 2.14-2+deb12u1 | Base image's OS package, not this project's dependency graph - present in `gcr.io/distroless/java-base-debian12:nonroot` the same way it was in `java21-debian12:nonroot` before the jlink switch (both pull the same underlying Debian package set for the modules this app's jlink runtime needs, e.g. `java.desktop`'s font-rendering libs). Will close automatically on the next monthly digest refresh once upstream rebuilds with the patched package. |
| `commons-fileupload`, `commons-io`, `micrometer-core`, `kafka-clients`, `lz4-java`, `postgresql` (42.7.12) | various | patch releases | Boot-BOM-managed or independently pinned; each needs its own `ext[]`/version override the same way Tomcat/Netty/Jackson did above, not yet done for these six - lower severity, deferred to the next pass rather than rushed in this PR. |
| `spring-ai-client-chat`, `spring-ai-model` | 1.0.0 | 1.0.7+ | See revert above - the fix requires the same spring-ai bump that crashed the MCP server. Needs a coordinated fix (likely also bumping/pinning `com.networknt:json-schema-validator` explicitly) before it can be taken. |
| `spring-boot`, `spring-core`, `spring-expression`, `spring-webflux`, `spring-webmvc`, `spring-security-web`, `spring-data-commons`, `spring-data-mongodb`, `spring-kafka`, `spring-graphql` | 3.3.13 / 6.1.21 / 6.3.10 / etc. | a Spring Boot **minor or major** version bump (3.3 → 3.5+/4.0, Spring Framework 6.1 → 6.2/7.0, Spring Security 6.3 → 6.5/7.0) | None of these have a same-line patch fix. Confirmed directly against Maven Central's `spring-boot-gradle-plugin` metadata: **3.3.13 is the newest release in the 3.3.x line**, so there's no smaller bump to take the way there was for Tomcat/Netty/Jackson. Every fix requires moving to a different minor/major release - a coordinated, whole-project upgrade (~a dozen interdependent library versions at once) with real compatibility risk across the entire codebase, out of scope for this Docker packaging deliverable. See "Why a waiver instead of 0 HIGH / 0 CRITICAL" above for the full reasoning. Tracked as follow-up work, not silently accepted risk. |

This waiver is scoped to image tag `0.1.0` / digest as built on 2026-08-27. A
rebuild that changes any of the versions above must re-run the scan and
update this table, not assume the waiver still applies.
