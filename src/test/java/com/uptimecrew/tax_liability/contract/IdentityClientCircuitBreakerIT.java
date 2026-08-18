package com.uptimecrew.tax_liability.contract;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.wireMockConfig;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;
import java.util.UUID;

import com.github.tomakehurst.wiremock.junit5.WireMockExtension;
import com.uptimecrew.tax_liability.clients.IdentityProfile;
import com.uptimecrew.tax_liability.clients.IdentityService;
import com.uptimecrew.tax_liability.security.ScopeAndRoleAuthoritiesConverter;

import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.junit.jupiter.api.extension.RegisterExtension;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * WireMock listens on port 8090; {@code application.yml}'s {@code identity.base-url} points the
 * Feign client at it. Tests 1 and 3 drive the happy path through a stubbed 200; test 2 drives the
 * breaker open by stubbing repeated 500s, then asserts subsequent calls hit the fallback (not
 * WireMock); test 4 asserts the OpenAPI document is a tested contract, not generated
 * documentation. Boots the same three-container Postgres+Mongo+Redis context as {@link
 * com.uptimecrew.tax_liability.TaxpayerSecurityIT}, since the full Spring context (JPA, Mongo
 * read model, Redis-backed idempotency) needs real datastores to start.
 */
@Testcontainers
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureMockMvc
@ActiveProfiles("test")
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class IdentityClientCircuitBreakerIT {

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> PG = new PostgreSQLContainer<>("postgres:16-alpine");

    @Container
    @ServiceConnection
    static final MongoDBContainer MONGO = new MongoDBContainer("mongo:7");

    @Container
    @ServiceConnection(name = "redis")
    static final GenericContainer<?> REDIS = new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    @RegisterExtension
    static final WireMockExtension WM = WireMockExtension.newInstance()
            .options(wireMockConfig().port(8090))
            .build();

    @Autowired
    private IdentityService identityService;

    @Autowired
    private CircuitBreakerRegistry circuitBreakerRegistry;

    @Autowired
    private MockMvc mvc;

    // The "identity" breaker is a singleton bean shared across every @Test method in this class;
    // force it back to CLOSED before each test so circuitOpens_after_repeated_5xx (which
    // deliberately trips it) cannot leak OPEN state into the tests that expect a real call.
    @BeforeEach
    void resetBreaker() {
        circuitBreakerRegistry.circuitBreaker("identity").transitionToClosedState();
    }

    @Test
    @Order(1)
    void getProfile_returnsBody_whenIdentityIs200() {
        WM.stubFor(get(urlEqualTo("/identity/u-1/profile"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"u-1\",\"displayName\":\"Pat\",\"region\":\"us-east-1\"}")));

        IdentityProfile p = identityService.getProfile("u-1");

        assertThat(p).isEqualTo(new IdentityProfile("u-1", "Pat", "us-east-1"));
    }

    @Test
    @Order(2)
    void summary_returns200_andIncludesProfileDisplayName() throws Exception {
        WM.stubFor(get(urlEqualTo("/identity/u-3/profile"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"u-3\",\"displayName\":\"Sam\",\"region\":\"us-east-1\"}")));

        mvc.perform(post("/api/v1/taxpayers/{id}/summary", "abc")
                        .header("Idempotency-Key", UUID.randomUUID().toString())
                        .with(readerJwt("u-3")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.displayName").value("Sam"));
    }

    @Test
    @Order(3)
    void circuitOpens_after_repeated_5xx() {
        WM.stubFor(get(urlEqualTo("/identity/u-2/profile"))
                .willReturn(aResponse().withStatus(500)));

        CircuitBreaker breaker = circuitBreakerRegistry.circuitBreaker("identity");
        for (int i = 0; i < 20 && breaker.getState() != CircuitBreaker.State.OPEN; i++) {
            identityService.getProfile("u-2");
        }

        assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.OPEN);

        // While OPEN, further calls short-circuit to the fallback without reaching WireMock;
        // record the request count and assert it does not grow.
        int callsBefore = WM.findAll(getRequestedFor(urlEqualTo("/identity/u-2/profile"))).size();
        IdentityProfile fallback = identityService.getProfile("u-2");
        int callsAfter = WM.findAll(getRequestedFor(urlEqualTo("/identity/u-2/profile"))).size();

        assertThat(callsAfter).isEqualTo(callsBefore);
        assertThat(fallback.displayName()).isEmpty();
    }

    @Test
    @Order(4)
    void openApiDoc_exposesV1Path() throws Exception {
        mvc.perform(MockMvcRequestBuilders.get("/v3/api-docs"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.paths['/api/v1/taxpayers/{id}']").exists())
                .andExpect(jsonPath("$.components.securitySchemes.bearer-jwt.scheme").value("bearer"));
    }

    private static RequestPostProcessor readerJwt(String subject) {
        return jwt()
                .jwt(j -> j.subject(subject).claim("scope", "taxpayers.read").claim("roles", List.of("TAXPAYER_READER")))
                .authorities(new ScopeAndRoleAuthoritiesConverter());
    }
}
