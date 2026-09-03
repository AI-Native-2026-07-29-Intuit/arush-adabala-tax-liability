package com.uptimecrew.tax_liability.observability;

import io.opentelemetry.api.trace.Span;
import io.prometheus.metrics.tracer.common.SpanContext;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Makes Micrometer attach OpenMetrics <em>exemplars</em> - the {@code # {trace_id="…"}} suffix on a
 * counter or histogram bucket - so a point on the RED dashboard's latency panel carries the id of a
 * trace that produced it, and clicking the diamond opens that trace in Tempo (W5 D5).
 *
 * <p><strong>Why this class has to exist.</strong> Everything else was already in place and the
 * feature still did not work: Prometheus runs with {@code --enable-feature=exemplar-storage}, the
 * dashboard's p50/p95/p99 targets set {@code exemplar: true}, and the OpenTelemetry agent produces
 * perfectly good spans. But Spring Boot only wires exemplar support into
 * {@code PrometheusMeterRegistry} when a {@link SpanContext} bean exists, and the bean it
 * autoconfigures is derived from <em>Micrometer Tracing</em>, which this application does not use -
 * tracing here comes from the Java agent, which Micrometer knows nothing about. The result was
 * silent and complete: {@code /actuator/prometheus} carried zero exemplars, Prometheus stored
 * nothing to click, and the panel rendered normally with no diamonds and no error anywhere.
 *
 * <p>This bridges the two: it reads the current span straight from the OpenTelemetry API, which the
 * agent populates, and hands Micrometer the ids it needs.
 *
 * <p>Registered unconditionally rather than per-profile: outside the cluster the W3 D5
 * OpenTelemetry Spring Boot starter populates the same {@link Span#current()}, so this works there
 * too, and where no SDK is active at all it degrades to "no exemplars" rather than to an error.
 */
@Configuration
public class OpenTelemetryExemplarConfig {

    /**
     * @return a {@link SpanContext} that reads the OpenTelemetry span currently in scope
     */
    @Bean
    public SpanContext openTelemetrySpanContext() {
        return new SpanContext() {

            @Override
            public String getCurrentTraceId() {
                io.opentelemetry.api.trace.SpanContext current = Span.current().getSpanContext();
                return current.isValid() ? current.getTraceId() : null;
            }

            @Override
            public String getCurrentSpanId() {
                io.opentelemetry.api.trace.SpanContext current = Span.current().getSpanContext();
                return current.isValid() ? current.getSpanId() : null;
            }

            /**
             * Only sampled spans are worth recording as exemplars: an exemplar pointing at a trace
             * the sampler dropped is a link to a 404 in Tempo, which is worse than no diamond at
             * all. With {@code parentbased_traceidratio} at 10% this returns true for the sampled
             * tenth (and for anything a caller marked sampled via {@code traceparent}).
             */
            @Override
            public boolean isCurrentSpanSampled() {
                return Span.current().getSpanContext().isSampled();
            }

            /**
             * A no-op: the hook exists so a tracer can flag "this span was chosen as an exemplar"
             * back onto the span itself. The Java agent offers no such mark, and nothing in this
             * stack reads it.
             */
            @Override
            public void markCurrentSpanAsExemplar() {
                // intentionally empty - see javadoc
            }
        };
    }
}
