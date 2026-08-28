# syntax=docker/dockerfile:1.7
#
# Four-stage build for the taxcalc-api Spring Boot service: healthcheck-builder
# + builder + extractor + distroless runtime.
# Build:
#   docker build \
#     --build-arg APP_VERSION=0.1.0 \
#     --build-arg GIT_SHA=$(git rev-parse --short HEAD) \
#     -t uptimecrew/taxcalc-api:0.1.0 .

# -------- 0. HEALTHCHECK-BUILDER STAGE --------
# Compiles the static Go probe binary the distroless runtime stage uses for
# HEALTHCHECK (distroless has no shell/curl/wget to run one inline). Discarded
# after the binary is copied out below; adds zero bytes to the final image.
FROM golang:1.25-alpine@sha256:1ae0735f00daffa3aaf1363a5184c0d2dc55c78e3db4ec70241cdac97bf84b59 AS healthcheck-builder
WORKDIR /src
COPY docker/healthcheck/ .
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o /healthcheck .

# -------- 1. BUILD STAGE --------
# Full JDK + Gradle. Discarded after bootJar is produced.
# JDK 17, not 21: build.gradle pins java.toolchain.languageVersion=17, and
# Gradle has no toolchain auto-provisioning configured, so the builder's JDK
# major version must match exactly. The resulting class files (bytecode
# release 17) run unmodified on the Java 21 JRE/distroless stages below.
FROM eclipse-temurin:17-jdk-jammy@sha256:400014962ad7224461f945bb1cc3d7d5a1927ce15b8245b72d9cedcda554cd2a AS builder
WORKDIR /workspace

# Cache wrapper + build files first (least-changing). Order matters:
# wrapper/gradle dir, then build files, THEN dependency pre-warm, THEN src.
COPY gradlew gradlew.bat ./
COPY gradle/ gradle/
COPY build.gradle settings.gradle ./

# Pre-warm the Gradle dependency cache (no source code yet) - the BuildKit
# cache mount keeps ~/.gradle across builds on top of the standard layer cache,
# so a code-only change below doesn't re-download the world. Scoped to
# compileClasspath + runtimeClasspath only (what `bootJar -x test` actually
# needs) rather than every configuration `dependencies` resolves by default -
# skipping test-only deps (Testcontainers, WireMock, Mockito, ...) cuts this
# step from ~73s to ~49s cold, measured with an otherwise-identical cache.
RUN --mount=type=cache,target=/root/.gradle,sharing=locked \
    ./gradlew --no-daemon dependencies --configuration compileClasspath && \
    ./gradlew --no-daemon dependencies --configuration runtimeClasspath

# Now copy source. Code-only changes invalidate only this layer and below.
COPY src/ src/

RUN --mount=type=cache,target=/root/.gradle,sharing=locked \
    ./gradlew --no-daemon bootJar -x test

# -------- 2. EXTRACT STAGE --------
# Run `layertools extract` on the bootJar. Tiny JRE only, no Gradle.
FROM eclipse-temurin:21-jre-jammy@sha256:eebd356ad7358b7094758e5787a6726f332917cfd56feab6457c56dab895cdbf AS extractor
WORKDIR /extract
COPY --from=builder /workspace/build/libs/tax_liability-*.jar app.jar
RUN java -Djarmode=layertools -jar app.jar extract --destination .

# -------- 3. RUNTIME STAGE --------
# Distroless JRE. No shell, no package manager, no user-management tools.
FROM gcr.io/distroless/java21-debian12:nonroot@sha256:7e37784d94dccbf5ccb195c73b295f5ad00cd266512dfbac12eb9c3c28f8077d

ARG APP_VERSION=0.0.0
ARG GIT_SHA=unset
LABEL org.opencontainers.image.title="taxcalc-api"
LABEL org.opencontainers.image.version="${APP_VERSION}"
LABEL org.opencontainers.image.revision="${GIT_SHA}"
LABEL org.opencontainers.image.source="https://github.com/AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# A plain `docker run` (bridge network, no --network host) gives the container its own network
# namespace, where `localhost` means the container itself - Postgres/Mongo on the host are only
# reachable via host.docker.internal. The `docker` Spring profile (application.yml) points there;
# baking it in here means it activates on every `docker run` with zero extra flags.
ENV SPRING_PROFILES_ACTIVE=docker

# Distroless :nonroot pre-creates UID 65532. Explicit USER for clarity, and it
# must come before ENTRYPOINT so the process itself (not just image metadata)
# actually runs as non-root.
USER 65532
WORKDIR /home/nonroot

# Copy the Spring Boot layered JAR dirs least-to-most-changing so a
# code-only rebuild only busts the "application" layer, not "dependencies".
COPY --from=extractor --chown=65532:65532 /extract/dependencies/          ./
COPY --from=extractor --chown=65532:65532 /extract/spring-boot-loader/    ./
COPY --from=extractor --chown=65532:65532 /extract/snapshot-dependencies/ ./
COPY --from=extractor --chown=65532:65532 /extract/application/           ./
COPY --from=healthcheck-builder --chown=65532:65532 /healthcheck ./healthcheck

EXPOSE 8080

# Distroless has no curl/wget - the HEALTHCHECK runs the static Go probe built
# above, which GETs /actuator/health/readiness and exits 0/1.
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
  CMD ["/home/nonroot/healthcheck"]

# Distroless has no shell - ENTRYPOINT MUST be exec form.
ENTRYPOINT ["java", "org.springframework.boot.loader.launch.JarLauncher"]
