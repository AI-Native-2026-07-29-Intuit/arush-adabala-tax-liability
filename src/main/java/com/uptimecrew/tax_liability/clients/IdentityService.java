package com.uptimecrew.tax_liability.clients;

import java.util.Objects;

import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * {@code @CircuitBreaker} is evaluated by a Spring AOP advisor on this service's proxy. Putting
 * the annotation directly on {@link TaxpayerIdentityClient} does NOT work, because the Feign
 * proxy stack short-circuits before the Resilience4j advisor gets a chance to intercept the call
 * — wrapping it in a plain {@code @Service} is what makes the breaker apply.
 */
@Service
public class IdentityService {

    private static final Logger LOG = LoggerFactory.getLogger(IdentityService.class);

    private final TaxpayerIdentityClient client;

    public IdentityService(TaxpayerIdentityClient client) {
        this.client = Objects.requireNonNull(client, "client must not be null");
    }

    @CircuitBreaker(name = "identity", fallbackMethod = "fallbackProfile")
    public IdentityProfile getProfile(String userId) {
        return client.getProfile(userId);
    }

    // Fallback signature: SAME return type, SAME parameters PLUS one trailing Throwable.
    // Resilience4j locates this by reflection.
    @SuppressWarnings("unused")
    private IdentityProfile fallbackProfile(String userId, Throwable t) {
        LOG.warn("identity breaker fallback for userId={} cause={}", userId, t.toString());
        return new IdentityProfile(userId, "", "unknown");
    }
}
