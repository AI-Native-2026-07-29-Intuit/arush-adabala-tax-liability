package com.uptimecrew.tax_liability.metrics;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;

import jakarta.servlet.ServletException;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/**
 * {@link InflightRequestsGauge} - the metric the SLO-derived HPA scales on.
 *
 * <p>Two of these tests guard properties that are invisible in normal operation and expensive
 * when wrong: the actuator exclusion (without it an idle pod reports a sixth of the HPA's scaling
 * target as load, holding replicas up overnight) and the decrement-on-throw (without it an error
 * spike ratchets the gauge permanently upward, because a gauge never decays).
 */
class InflightRequestsGaugeTest {

    private MeterRegistry registry;
    private InflightRequestsGauge filter;

    @BeforeEach
    void setUp() {
        registry = new SimpleMeterRegistry();
        filter = new InflightRequestsGauge(registry);
    }

    private double gaugeValue() {
        Gauge gauge = registry.find(InflightRequestsGauge.METER_NAME).gauge();
        assertThat(gauge).as("gauge %s is registered", InflightRequestsGauge.METER_NAME).isNotNull();
        return gauge.value();
    }

    @Test
    @DisplayName("the gauge is registered under the name the Adapter rule and HPA both use")
    void gauge_is_registered() {
        assertThat(registry.find(InflightRequestsGauge.METER_NAME).gauge()).isNotNull();
        assertThat(gaugeValue()).isZero();
    }

    @Test
    @DisplayName("a null registry is rejected at construction")
    void null_registry_rejected() {
        assertThatThrownBy(() -> new InflightRequestsGauge(null))
                .isInstanceOf(NullPointerException.class)
                .hasMessageContaining("registry");
    }

    @Test
    @DisplayName("an in-flight request is visible to a scrape that happens mid-request")
    void counts_a_request_while_it_is_in_flight() throws ServletException, IOException {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/v1/taxpayers");
        MockHttpServletResponse response = new MockHttpServletResponse();

        // The assertion has to happen INSIDE the chain: by the time doFilter returns, the
        // finally block has already decremented and the gauge is back to 0. A test that only
        // looked before and after would pass against a filter that counted nothing at all.
        double[] observedDuringRequest = new double[1];
        MockFilterChain chain = new MockFilterChain() {
            @Override
            public void doFilter(jakarta.servlet.ServletRequest req, jakarta.servlet.ServletResponse res) {
                observedDuringRequest[0] = gaugeValue();
            }
        };

        filter.doFilter(request, response, chain);

        assertThat(observedDuringRequest[0]).isEqualTo(1.0d);
        assertThat(gaugeValue()).isZero();
    }

    @Test
    @DisplayName("an actuator scrape does not count itself, so an idle pod reports exactly 0")
    void actuator_scrape_is_not_counted() throws ServletException, IOException {
        MockHttpServletRequest scrape = new MockHttpServletRequest("GET", "/actuator/prometheus");
        double[] observed = new double[1];
        MockFilterChain chain = new MockFilterChain() {
            @Override
            public void doFilter(jakarta.servlet.ServletRequest req, jakarta.servlet.ServletResponse res) {
                observed[0] = gaugeValue();
            }
        };

        filter.doFilter(scrape, new MockHttpServletResponse(), chain);

        assertThat(observed[0])
                .as("the scrape must not see itself, or every idle pod floors at 1")
                .isZero();
        assertThat(gaugeValue()).isZero();
    }

    @Test
    @DisplayName("kubelet probe traffic is excluded too")
    void probe_traffic_is_not_counted() {
        MockHttpServletRequest liveness = new MockHttpServletRequest("GET", "/actuator/health/liveness");
        MockHttpServletRequest readiness = new MockHttpServletRequest("GET", "/actuator/health/readiness");
        MockHttpServletRequest work = new MockHttpServletRequest("GET", "/api/v1/taxpayers/tp-2026-0001");

        assertThat(InflightRequestsGauge.isExcluded(liveness)).isTrue();
        assertThat(InflightRequestsGauge.isExcluded(readiness)).isTrue();
        assertThat(InflightRequestsGauge.isExcluded(work)).isFalse();
    }

    @Test
    @DisplayName("a request that throws still decrements, so the gauge cannot ratchet upward")
    void decrements_even_when_the_chain_throws() {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/v1/taxpayers");
        MockFilterChain exploding = new MockFilterChain() {
            @Override
            public void doFilter(jakarta.servlet.ServletRequest req, jakarta.servlet.ServletResponse res) {
                throw new IllegalStateException("downstream blew up");
            }
        };

        assertThatThrownBy(() -> filter.doFilter(request, new MockHttpServletResponse(), exploding))
                .isInstanceOf(IllegalStateException.class);

        assertThat(gaugeValue())
                .as("a leaked +1 never decays on a gauge - an error spike would hold replicas up for hours")
                .isZero();
        assertThat(filter.current()).isZero();
    }

    @Test
    @DisplayName("concurrent requests are counted together, which is what 'per-replica concurrency' means")
    void counts_concurrent_requests() throws Exception {
        int concurrency = 4;
        CountDownLatch allInFlight = new CountDownLatch(concurrency);
        CountDownLatch release = new CountDownLatch(1);

        Thread[] threads = new Thread[concurrency];
        for (int i = 0; i < concurrency; i++) {
            threads[i] = new Thread(() -> {
                MockFilterChain chain = new MockFilterChain() {
                    @Override
                    public void doFilter(jakarta.servlet.ServletRequest req, jakarta.servlet.ServletResponse res) {
                        allInFlight.countDown();
                        try {
                            release.await(5, TimeUnit.SECONDS);
                        } catch (InterruptedException ex) {
                            Thread.currentThread().interrupt();
                        }
                    }
                };
                try {
                    filter.doFilter(new MockHttpServletRequest("GET", "/api/v1/taxpayers/tp-2026-0001"),
                            new MockHttpServletResponse(), chain);
                } catch (ServletException | IOException ex) {
                    throw new IllegalStateException(ex);
                }
            });
            threads[i].start();
        }

        assertThat(allInFlight.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(gaugeValue()).isEqualTo((double) concurrency);

        release.countDown();
        for (Thread thread : threads) {
            thread.join(5_000L);
        }
        assertThat(gaugeValue()).isZero();
    }
}
