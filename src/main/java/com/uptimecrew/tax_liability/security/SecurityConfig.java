package com.uptimecrew.tax_liability.security;

import java.util.Objects;

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
 */
@Configuration
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
