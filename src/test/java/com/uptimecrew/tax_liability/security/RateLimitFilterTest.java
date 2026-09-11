package com.uptimecrew.tax_liability.security;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * Route selection for the LLM rate limit (W6 D4 Task 2).
 *
 * <p>Rate limiting an LLM route is a cost control: it bounds the worst case of a retry storm
 * <em>before</em> the money is spent, which is the one thing neither the in-app cost log (which
 * only records) nor the Anthropic Console spend limit (which only stops things after the fact)
 * can do. So a paid route that this filter does not match is a hole in the cost controls, and
 * that is what these cases pin.
 */
class RateLimitFilterTest {

    @ParameterizedTest(name = "{0} is rate limited")
    @ValueSource(strings = {
        "/api/v1/taxpayers/txp-1/summary",
        "/api/v1/taxpayers/txp-1/explanation",
        // W6 D4 Task 2: the LLM proxy, which lives outside /api/ and so is matched by its own rule.
        "/v1/completions",
    })
    void everyPaidRouteIsRateLimited(String uri) {
        assertThat(RateLimitFilter.isLlmRoute(uri)).isTrue();
    }

    @ParameterizedTest(name = "{0} is not rate limited")
    @ValueSource(strings = {
        "/api/v1/taxpayers/txp-1",
        "/api/v1/taxpayers",
        "/actuator/health",
        "/actuator/prometheus",
        // Outside /api/ entirely - the bucket lookup needs a resolved JWT principal, and these
        // routes are permitted unauthenticated.
        "/summary",
        "/explanation",
    })
    void freeAndUnauthenticatedRoutesAreNot(String uri) {
        assertThat(RateLimitFilter.isLlmRoute(uri)).isFalse();
    }

    @Test
    void nullUriIsNotAnLlmRoute() {
        assertThat(RateLimitFilter.isLlmRoute(null)).isFalse();
    }
}
