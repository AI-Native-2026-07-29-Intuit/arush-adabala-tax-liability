package com.uptimecrew.tax_liability.observability;

import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.OpenTelemetry;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

/**
 * Bridges the OpenTelemetry Java agent's SDK into the Spring context, for the {@code k8s} profile
 * only (W5 D5).
 *
 * <p>In the cluster the agent - attached through {@code JAVA_TOOL_OPTIONS} by
 * {@code manifests/observability/taxcalc-api-deployment-patch.yaml} - owns all automatic
 * instrumentation, so {@code application.yml}'s {@code k8s} profile sets {@code otel.sdk.disabled:
 * true} to stop the W3 D5 OpenTelemetry Spring Boot starter from building a second SDK and
 * double-instrumenting every request.
 *
 * <p>That switch has a side effect worth naming: the starter's fallback configuration then
 * publishes {@link OpenTelemetry#noop()} as the {@code OpenTelemetry} bean. Application code that
 * injects that bean - {@code TaxLiabilityService}, which captures the current trace context onto
 * each outbox row so {@code OutboxPublisher}'s later async sweep can rejoin the originating trace
 * (W3 D5) - would then silently capture nothing. Nothing fails; the traces just stop connecting,
 * which is the hardest class of observability bug to notice.
 *
 * <p>This configuration replaces that no-op with {@link GlobalOpenTelemetry#get()}, which the
 * agent installs as the global instance at startup, so injected code and agent-instrumented code
 * share one SDK and one trace. An explicit {@code @Bean} wins over the starter's
 * {@code @ConditionalOnMissingBean} fallback, so no ordering configuration is needed.
 *
 * <p>Outside the {@code k8s} profile this class does not apply at all and the starter's own SDK
 * bean is used, exactly as it was before W5 D5.
 */
@Configuration
@Profile("k8s")
public class AgentOpenTelemetryConfig {

    private static final Logger LOG = LoggerFactory.getLogger(AgentOpenTelemetryConfig.class);

    /**
     * @return the OpenTelemetry instance the Java agent registered globally; a no-op instance if
     *         the profile is active but no agent is attached (running the {@code k8s} profile
     *         outside Kubernetes, say), which degrades to "no traces" rather than to a failed
     *         context startup
     */
    @Bean
    public OpenTelemetry openTelemetry() {
        OpenTelemetry global = GlobalOpenTelemetry.get();
        // Logged once at startup so "are we on the agent or the starter?" is answerable from the
        // pod's own logs instead of by reading two config files and a Deployment patch.
        LOG.info("k8s profile: OpenTelemetry bean bound to the agent's global instance ({})",
                global.getClass().getName());
        return global;
    }
}
