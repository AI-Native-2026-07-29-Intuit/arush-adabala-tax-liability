# taxcalc-orders/Dockerfile
#
# Runtime only: the jar is built on the host (or by CI) with `./gradlew -p taxcalc-orders bootJar`
# and copied in. Building inside the image would mean downloading Gradle and the whole dependency
# graph on every rebuild, through whatever proxy the builder happens to sit behind, to produce an
# artefact the host can already make in seconds from a warm cache.
#
# The consequence is stated rather than assumed: this Dockerfile REQUIRES build/libs/taxcalc-orders.jar
# to exist. The E2E builds it first and fails with that exact message if the build fails.
FROM eclipse-temurin:17-jre

# A non-root user. The jar is owned by root and readable by everyone, so a process that is
# compromised cannot rewrite the code it is running.
RUN useradd --create-home --uid 10001 orders

WORKDIR /app
COPY build/libs/taxcalc-orders.jar /app/taxcalc-orders.jar

USER orders
EXPOSE 8080

# Container-aware heap sizing. Without it the JVM reads the HOST's memory and sizes a heap the
# container's cgroup limit will not honour, which surfaces as an OOM kill rather than as a
# GC pause - a confusing failure in a test that looks like flakiness.
ENV JAVA_OPTS="-XX:MaxRAMPercentage=75.0"

# The readiness signal the E2E polls. Actuator's health endpoint reports DOWN until Flyway has
# finished and the datasource answers, which is exactly the condition the test needs to wait for -
# a TCP port that accepts before the schema exists would let the first request race the migration.
HEALTHCHECK --interval=5s --timeout=3s --start-period=30s --retries=10 \
    CMD ["sh", "-c", "wget -qO- http://127.0.0.1:8080/actuator/health | grep -q '\"status\":\"UP\"'"]

ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/taxcalc-orders.jar"]
