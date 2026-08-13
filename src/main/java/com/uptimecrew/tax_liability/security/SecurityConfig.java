package com.uptimecrew.tax_liability.security;

import java.util.Collection;
import java.util.List;
import java.util.Objects;
import java.util.stream.Stream;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.oauth2.server.resource.authentication.JwtGrantedAuthoritiesConverter;
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
                        .requestMatchers("/actuator/health").permitAll()
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
     * Maps BOTH the standard {@code scope} claim (space-delimited, -&gt; {@code SCOPE_*}
     * authorities via {@link JwtGrantedAuthoritiesConverter}) AND a custom {@code roles} claim
     * (-&gt; {@code ROLE_*} authorities). {@link com.uptimecrew.tax_liability.api.TaxpayerController}'s
     * {@code @PreAuthorize} SpEL checks both, so both must be present on the resulting token.
     */
    private JwtAuthenticationConverter jwtAuthenticationConverter() {
        JwtGrantedAuthoritiesConverter scopeAuthoritiesConverter = new JwtGrantedAuthoritiesConverter();
        scopeAuthoritiesConverter.setAuthorityPrefix("SCOPE_");
        scopeAuthoritiesConverter.setAuthoritiesClaimName("scope");

        JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
        converter.setJwtGrantedAuthoritiesConverter(jwt -> {
            Collection<GrantedAuthority> scopeAuthorities = scopeAuthoritiesConverter.convert(jwt);
            List<String> roles = jwt.getClaimAsStringList("roles");
            Stream<GrantedAuthority> roleAuthorities = (roles == null ? Stream.<String>empty() : roles.stream())
                    .map(role -> (GrantedAuthority) new SimpleGrantedAuthority("ROLE_" + role));
            return Stream.concat(scopeAuthorities.stream(), roleAuthorities).toList();
        });
        return converter;
    }
}
