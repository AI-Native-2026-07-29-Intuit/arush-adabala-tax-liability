package com.uptimecrew.tax_liability;

import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.Statement;
import java.util.List;

import com.uptimecrew.tax_liability.service.TaxLiabilityService;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * Proves the W3 D1 edge-security shape end to end against the real {@link
 * com.uptimecrew.tax_liability.security.SecurityConfig} filter chain: the full 200/401/403/429
 * matrix for {@link com.uptimecrew.tax_liability.api.TaxpayerController}, backed by the same
 * three-container Postgres+Mongo+Redis setup as W2 D5's {@link TaxpayerPolyglotIT}.
 *
 * <p>{@code spring-security-test}'s {@code jwt()} request post-processor builds its
 * authentication directly from a default {@code JwtGrantedAuthoritiesConverter} (which only reads
 * the standard {@code scope}/{@code scp} claim), rather than routing through the app's registered
 * {@link com.uptimecrew.tax_liability.security.SecurityConfig} converter bean. Each test therefore
 * pairs the JWT claims a real IdP would issue with an explicit {@code .authorities(...)} call so
 * the mocked token carries the same {@code SCOPE_*}/{@code ROLE_*} authorities that converter
 * would produce from those claims.
 */
@Testcontainers
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class TaxpayerSecurityIT {

    private static final String READ_SCOPE = "taxpayers.read";
    private static final String READER_ROLE = "TAXPAYER_READER";
    private static final String SEEDED_TAXPAYER_ID = "security-it-taxpayer";

    private static volatile boolean taxpayerSeeded = false;

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> PG = new PostgreSQLContainer<>("postgres:16-alpine");

    @Container
    @ServiceConnection
    static final MongoDBContainer MONGO = new MongoDBContainer("mongo:7");

    @Container
    @ServiceConnection(name = "redis")
    static final GenericContainer<?> REDIS = new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    @Autowired
    private MockMvc mvc;

    @Autowired
    private TaxLiabilityService service;

    @BeforeAll
    static void applyPostgresSchema() throws Exception {
        try (Connection conn = TestPostgresConnections.openWithRetry(PG);
                Statement stmt = conn.createStatement()) {
            stmt.execute(Files.readString(Path.of("db/V1__schema.sql")));
        }
        // Mongo and Redis are schemaless - no equivalent step is needed for them.
    }

    // Seeds once (not per-test): the id only needs to exist for the getById tests, and
    // TaxLiabilityService.computeLiability is not idempotent (it always inserts a fresh
    // Liability row), so calling it once keeps the fixture data minimal and predictable.
    @BeforeEach
    void seedTaxpayerOnce() {
        if (!taxpayerSeeded) {
            service.computeLiability(SEEDED_TAXPAYER_ID, "Ada Lovelace", "SINGLE", new BigDecimal("75000.00"));
            taxpayerSeeded = true;
        }
    }

    @Test
    void getById_returns200_whenAuthenticatedWithScopeAndRole() throws Exception {
        mvc.perform(get("/api/taxpayers/{id}", SEEDED_TAXPAYER_ID).with(readerJwt("full-access-user")))
                .andExpect(status().isOk());
    }

    @Test
    void getById_returns401_whenAnonymous() throws Exception {
        mvc.perform(get("/api/taxpayers/{id}", SEEDED_TAXPAYER_ID))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void getById_returns403_whenJwtMissingRole() throws Exception {
        RequestPostProcessor scopeOnlyNoRole = jwt()
                .jwt(j -> j.subject("scope-only-user").claim("scope", READ_SCOPE).claim("roles", List.of()))
                .authorities(new SimpleGrantedAuthority("SCOPE_" + READ_SCOPE));

        mvc.perform(get("/api/taxpayers/{id}", SEEDED_TAXPAYER_ID).with(scopeOnlyNoRole))
                .andExpect(status().isForbidden());
    }

    @Test
    void summary_returns429_after10Calls() throws Exception {
        RequestPostProcessor rateLimitedCaller = readerJwt("rate-limit-user");

        for (int call = 1; call <= 10; call++) {
            mvc.perform(get("/api/taxpayers/{id}/summary", SEEDED_TAXPAYER_ID).with(rateLimitedCaller))
                    .andExpect(status().isOk());
        }

        mvc.perform(get("/api/taxpayers/{id}/summary", SEEDED_TAXPAYER_ID).with(rateLimitedCaller))
                .andExpect(status().isTooManyRequests())
                .andExpect(header().string("Retry-After", "60"));
    }

    private static RequestPostProcessor readerJwt(String subject) {
        return jwt()
                .jwt(j -> j.subject(subject).claim("scope", READ_SCOPE).claim("roles", List.of(READER_ROLE)))
                .authorities(
                        new SimpleGrantedAuthority("SCOPE_" + READ_SCOPE),
                        new SimpleGrantedAuthority("ROLE_" + READER_ROLE));
    }
}
