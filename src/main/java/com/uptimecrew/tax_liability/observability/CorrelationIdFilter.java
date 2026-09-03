package com.uptimecrew.tax_liability.observability;

import java.io.IOException;
import java.util.UUID;

import io.opentelemetry.api.trace.Span;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Threads one caller-visible id through all three observability pillars (W5 D5).
 *
 * <p>The id arrives as an {@code X-Correlation-Id} request header (a support ticket quoting "the
 * request that failed" has one) or is generated here when absent, and is then published three
 * ways, each deliberately different:
 *
 * <ul>
 *   <li><strong>MDC</strong> - so {@code logback-spring.xml}'s {@code k8s} profile serialises it
 *       as a top-level {@code correlationId} property on every JSON log line the request emits.</li>
 *   <li><strong>Span attribute</strong> - so the same request is findable in Tempo by that id
 *       ({@code {span.correlation.id = "..."}}), which is what closes the loop between a log line
 *       someone pasted into a ticket and the trace that produced it.</li>
 *   <li><strong>Response header</strong> - so the caller can quote the id back without having to
 *       be told what it was.</li>
 * </ul>
 *
 * <p>It is emphatically <em>not</em> published as a metric label: one time series per correlation
 * id is one time series per request, which is how a Prometheus instance runs out of memory. See
 * {@code manifests/observability/LABELS.md}.
 *
 * <p>Ordered at {@link Ordered#HIGHEST_PRECEDENCE} so it wraps Spring Security's filter chain
 * (registered at -100) rather than sitting inside it. A request rejected with 401 before it ever
 * reaches a controller is exactly the request an operator most needs to find later, and it gets a
 * correlation id, a log line and a span attribute like any other.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public final class CorrelationIdFilter extends OncePerRequestFilter {

    /** Inbound and outbound header name; matched case-insensitively by the servlet container. */
    public static final String HEADER = "X-Correlation-Id";

    /** MDC key, and therefore the JSON property name on every log line. */
    public static final String MDC_KEY = "correlationId";

    /** Span attribute key. Dotted, following OpenTelemetry attribute naming. */
    public static final String SPAN_ATTRIBUTE = "correlation.id";

    private static final Logger LOG = LoggerFactory.getLogger(CorrelationIdFilter.class);

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
            FilterChain chain) throws ServletException, IOException {
        String correlationId = resolve(request.getHeader(HEADER));
        MDC.put(MDC_KEY, correlationId);
        // Span.current() is the server span the OpenTelemetry agent (or, outside the cluster, the
        // Spring Boot starter's servlet instrumentation) opened for this request. When no SDK is
        // active this is a no-op invalid span, so the filter stays safe in tests and in plain
        // `java -jar` runs with tracing disabled.
        Span.current().setAttribute(SPAN_ATTRIBUTE, correlationId);
        response.setHeader(HEADER, correlationId);

        long startedNanos = System.nanoTime();
        try {
            chain.doFilter(request, response);
        } finally {
            // Logged after the chain so the status is known: this is the one line guaranteed to
            // exist for every request, including the ones rejected before any controller ran.
            LOG.info("http request completed method={} uri={} status={} durationMs={}",
                    request.getMethod(), request.getRequestURI(), response.getStatus(),
                    (System.nanoTime() - startedNanos) / 1_000_000);
            // Tomcat pools request threads; an MDC entry left behind would reappear on an
            // unrelated later request and attribute its log lines to the wrong caller.
            MDC.remove(MDC_KEY);
        }
    }

    /**
     * @param header the inbound header value, may be null or blank
     * @return the caller's id when it supplied a usable one, otherwise a fresh UUID v4
     */
    private static String resolve(String header) {
        if (header == null || header.isBlank()) {
            return UUID.randomUUID().toString();
        }
        // Trimmed and length-capped: this value is echoed into a response header and into every
        // log line for the request, so an unbounded caller-controlled string is both a log-volume
        // problem and a header-injection surface. Control characters (CR/LF above all) are
        // rejected outright rather than stripped - a caller sending them is not a caller whose id
        // is worth preserving.
        String trimmed = header.trim();
        if (trimmed.length() > 128 || !trimmed.chars().allMatch(c -> c >= 0x20 && c < 0x7F)) {
            return UUID.randomUUID().toString();
        }
        return trimmed;
    }
}
