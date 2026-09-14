package com.uptimecrew.tax_liability.security;

import java.util.Objects;

import org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.oauth2.server.resource.web.authentication.BearerTokenAuthenticationFilter;
import org.springframework.security.web.SecurityFilterChain;

/**
 * Edge security for the read path: every {@code /api/**} request must arrive with a valid Bearer
 * JWT, resolved as an OAuth2 Resource Server token, before it reaches {@link
 * com.uptimecrew.tax_liability.api.TaxpayerController}. {@link RateLimitFilter} (W3 D1 Task 3) is
 * registered after the bearer-token filter so it can key its Bucket4j bucket off the already
 * resolved JWT subject.
 *
 * <h2>W6 D5: why this is conditional on a servlet application</h2>
 *
 * <p>The {@code worker} run mode (W6 D5 Task 1) runs this same image with
 * {@code spring.main.web-application-type=none}. Without the condition below, that context fails
 * to start:
 *
 * <pre>
 *   Parameter 0 of method setFilterChains in WebSecurityConfiguration required a bean of type
 *   'org.springframework.security.oauth2.jwt.JwtDecoder' that could not be found.
 * </pre>
 *
 * <p>The cause is a mismatch in how two things are conditioned. Boot's
 * {@code OAuth2ResourceServerAutoConfiguration}, which supplies the {@link
 * org.springframework.security.oauth2.jwt.JwtDecoder}, is
 * {@code @ConditionalOnWebApplication(type = SERVLET)} and therefore correctly does nothing in a
 * worker. {@code @EnableWebSecurity} carries no such condition, so it imports
 * {@code WebSecurityConfiguration} anyway and that class demands the filter chain this class
 * declares - which needs the decoder that was, correctly, never created.
 *
 * <p>The error names {@code JwtDecoder}, which sends you looking at issuer configuration and
 * Secrets. Nothing in the message mentions the run mode, and the api pods running the identical
 * image are perfectly healthy at the time - so the natural first conclusion is that the worker's
 * environment is missing a value, rather than that a worker should not be building an HTTP filter
 * chain in the first place.
 *
 * <p>Edge security is a property of the HTTP edge. A process with no HTTP edge has nothing to
 * secure here, and its actual authorisation boundary is the Kafka broker's.
 */
@Configuration
@ConditionalOnWebApplication(type = ConditionalOnWebApplication.Type.SERVLET)
@EnableWebSecurity
@EnableMethodSecurity(prePostEnabled = true) // (1) turns @PreAuthorize on for TaxpayerController
public class SecurityConfig {

    private final RateLimitFilter rateLimitFilter;

    public SecurityConfig(RateLimitFilter rateLimitFilter) {
        this.rateLimitFilter = Objects.requireNonNull(rateLimitFilter, "rateLimitFilter must not be null");
    }

    @Bean
    public SecurityFilterChain apiSecurity(HttpSecurity http) throws Exception {
        http
                // (2) Stateless Bearer-only API; no session cookie is ever issued, so there is no
                //     CSRF surface to protect. If this app ever grows a cookie-based login flow,
                //     re-enable CSRF for that flow first.
                .csrf(csrf -> csrf.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth
                        // Wildcarded: Spring Boot serves the liveness/readiness groups as
                        // sub-paths of this same endpoint (/actuator/health/readiness,
                        // /actuator/health/liveness), and Docker/Kubernetes probes hit those
                        // sub-paths directly, with no bearer token to present.
                        .requestMatchers("/actuator/health/**").permitAll()
                        // (1c) W5 D5: the Prometheus scrape target. `anyRequest().denyAll()`
                        // below is not a 401 the scraper could authenticate past - it is a flat
                        // 403, and the Prometheus Operator reports a target that is DOWN rather
                        // than a config error, so this matcher is what makes the ServiceMonitor
                        // work at all. Unauthenticated on purpose: the exposition carries only
                        // aggregate series whose labels are a deliberately bounded set (no
                        // taxpayer ids, no user ids - see manifests/observability/LABELS.md),
                        // and it is reachable only from inside the cluster (the Ingress routes
                        // /api, and no rule maps /actuator).
                        .requestMatchers("/actuator/prometheus").permitAll()
                        .requestMatchers("/v3/api-docs/**", "/swagger-ui/**", "/swagger-ui.html").permitAll()
                        // (1a) Spring AI's MCP WebMVC server (W3 D3): the local-only tool surface
                        // Claude Code connects to over SSE. Unauthenticated like the two matchers
                        // above (a local dev/tooling entry point, not taxpayer data), and narrow by
                        // construction - TaxpayerMcpServer exposes exactly one read-only lookup.
                        .requestMatchers("/sse", "/mcp/message").permitAll()
                        // (1b) Spring for GraphQL (W3 D4): the in-browser GraphiQL UI and the SDL
                        // introspection endpoint, unauthenticated like the tooling entry points above.
                        .requestMatchers("/graphql", "/graphql/**", "/graphiql/**").permitAll()
                        .requestMatchers("/api/**").authenticated()
                        // (2) The LLM proxy (W6 D4 Task 2) answers on /v1/completions rather than
                        // under /api/**, because that is the address a proxy is expected to serve.
                        // It needs its own matcher precisely because the fallthrough below is
                        // denyAll - a new prefix is unreachable until it is named here, which is
                        // the safe direction for that default to fail in.
                        .requestMatchers("/v1/**").authenticated()
                        .anyRequest().denyAll())
                .oauth2ResourceServer(oauth2 -> oauth2
                        .jwt(jwt -> jwt.jwtAuthenticationConverter(jwtAuthenticationConverter())))
                // (3) Place the rate-limit filter AFTER the bearer-token filter so the
                //     JWT principal is already resolved when the bucket lookup runs.
                .addFilterAfter(rateLimitFilter, BearerTokenAuthenticationFilter.class);

        return http.build();
    }

    /**
     * {@link com.uptimecrew.tax_liability.api.TaxpayerController}'s {@code @PreAuthorize} SpEL
     * checks both a {@code SCOPE_*} and a {@code ROLE_*} authority, so both must be present on the
     * resulting token; see {@link ScopeAndRoleAuthoritiesConverter} for how they're derived.
     */
    private JwtAuthenticationConverter jwtAuthenticationConverter() {
        JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
        converter.setJwtGrantedAuthoritiesConverter(new ScopeAndRoleAuthoritiesConverter());
        return converter;
    }
}
