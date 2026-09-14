package com.uptimecrew.tax_liability.metrics;

import java.io.IOException;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicInteger;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Publishes {@value #METER_NAME}, the per-replica count of HTTP requests currently being served
 * (W6 D5 Task 2). This is the signal the SLO-derived HPA scales {@code taxcalc-api} on, read out
 * of Prometheus through the Prometheus Adapter and into the custom-metrics API.
 *
 * <h2>Why this metric and not {@code targetCPUUtilizationPercentage}</h2>
 *
 * <p>{@code taxcalc-api}'s slowest path is an outbound call to the Anthropic API. A pod serving
 * that request is parked in a socket read: it holds a request, a thread and a connection, and it
 * burns almost no CPU doing so. Under LLM-bound load p99 latency climbs straight through the
 * 500 ms SLO while CPU utilisation stays flat in the teens, so a CPU-target HPA never scales -
 * it is not misconfigured, it is measuring the wrong resource. In-flight count is what actually
 * tracks saturation for this service, because it counts exactly the thing that runs out.
 *
 * <p>The HPA's {@code averageValue: 6} comes from a single-replica saturation ramp: one pod holds
 * p99 under 500 ms up to roughly six concurrent requests and breaks above it. That makes the
 * target SLO-derived rather than invented - it is the concurrency at which this service stops
 * meeting the W5 D5 objective, measured, not a round number.
 *
 * <h2>The actuator scrape must not count itself</h2>
 *
 * <p>{@code /actuator/prometheus} is an HTTP request like any other, and it is in flight at the
 * exact moment the gauge it is collecting is read. Counting it puts a permanent floor of 1 (more,
 * with several Prometheus replicas or a concurrent probe) under a gauge whose whole job is to sit
 * at 0 on an idle pod.
 *
 * <p>That floor is not a rounding error here, it is the difference between a working HPA and a
 * cost bug. With {@code averageValue: 6} and a floor of 1, a completely idle Deployment reports
 * one sixth of its scaling target as real load, which holds replicas up that would otherwise come
 * down - and it does so most visibly at minimum traffic, i.e. overnight. Probe and scrape traffic
 * is therefore excluded by path, and {@code InflightRequestsGaugeTest} asserts an
 * {@code /actuator/prometheus} request leaves the gauge at 0.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class InflightRequestsGauge extends OncePerRequestFilter {

    /**
     * The meter name. Micrometer's Prometheus registry renders dots as underscores, so this is
     * scraped as {@code taxcalc_inflight_requests} - which is the name the Prometheus Adapter
     * rule, the HPA's {@code metric.name} and the k6 evidence all use.
     */
    public static final String METER_NAME = "taxcalc.inflight.requests";

    /**
     * Path prefix whose traffic is excluded from the count. Every actuator route is excluded, not
     * just {@code /actuator/prometheus}: the kubelet's liveness and readiness probes hit
     * {@code /actuator/health/**} once or twice a second per pod, and they are infrastructure
     * chatter that says nothing about whether this replica is saturated with user work.
     */
    static final String EXCLUDED_PREFIX = "/actuator";

    private final AtomicInteger inflight = new AtomicInteger();

    /**
     * Register the gauge against the application's registry.
     *
     * <p>The gauge is bound to the {@link AtomicInteger} by strong reference through
     * {@link Gauge.Builder}, which matters: Micrometer holds gauge state <em>weakly</em> by
     * default, and a gauge whose source object is collected reports {@code NaN} forever after
     * with no error anywhere. Keeping {@code inflight} as a field of this singleton filter is
     * what stops that.
     *
     * @param registry the Micrometer registry to publish into; never null
     * @throws NullPointerException if {@code registry} is null
     */
    public InflightRequestsGauge(MeterRegistry registry) {
        Objects.requireNonNull(registry, "registry must not be null");
        Gauge.builder(METER_NAME, inflight, AtomicInteger::doubleValue)
                .description("HTTP requests currently in flight on this replica, excluding "
                        + EXCLUDED_PREFIX + " traffic")
                .strongReference(true)
                .register(registry);
    }

    /**
     * Count one request for its whole duration.
     *
     * <p>The decrement is in a {@code finally} block. Without it, any request that throws leaks a
     * permanent +1 into the gauge, and since this is a gauge and not a counter that leak never
     * decays - an error spike would ratchet the reported concurrency up and leave the HPA holding
     * replicas for load that finished hours ago.
     *
     * @param request     the incoming request; never null
     * @param response    the response; never null
     * @param filterChain the rest of the chain; never null
     * @throws ServletException if the downstream chain does
     * @throws IOException      if the downstream chain does
     */
    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        if (isExcluded(request)) {
            filterChain.doFilter(request, response);
            return;
        }
        inflight.incrementAndGet();
        try {
            filterChain.doFilter(request, response);
        } finally {
            inflight.decrementAndGet();
        }
    }

    /**
     * Whether this request is infrastructure traffic that must not be counted.
     *
     * @param request the incoming request; never null
     * @return true for actuator scrapes and kubelet probes
     */
    static boolean isExcluded(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path != null && path.startsWith(EXCLUDED_PREFIX);
    }

    /**
     * Current in-flight count on this replica.
     *
     * <p>Exposed for tests; the production reader is the Prometheus scrape.
     *
     * @return the number of non-actuator requests currently being served, never negative in
     *         normal operation
     */
    public int current() {
        return inflight.get();
    }

    @Override
    public String toString() {
        return "InflightRequestsGauge{meter=" + METER_NAME + ", current=" + current() + "}";
    }
}
