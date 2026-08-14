package com.uptimecrew.tax_liability.security;

import java.util.Collection;
import java.util.List;
import java.util.stream.Stream;

import org.springframework.core.convert.converter.Converter;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtGrantedAuthoritiesConverter;

/**
 * Maps a {@link Jwt} to the authorities {@code @PreAuthorize} checks against: the standard
 * {@code scope} claim (space-delimited, via {@link JwtGrantedAuthoritiesConverter}) into {@code
 * SCOPE_*} authorities, AND a custom {@code roles} claim into {@code ROLE_*} authorities. Named
 * and reusable rather than an inline lambda so {@code TaxpayerSecurityIT} can pass this same
 * instance to {@code spring-security-test}'s {@code jwt()} post-processor and get authorities
 * derived from claims exactly the way {@link SecurityConfig} derives them in production, instead
 * of restating the expected authorities by hand in every test.
 */
public final class ScopeAndRoleAuthoritiesConverter implements Converter<Jwt, Collection<GrantedAuthority>> {

    private final JwtGrantedAuthoritiesConverter scopeAuthoritiesConverter;

    public ScopeAndRoleAuthoritiesConverter() {
        this.scopeAuthoritiesConverter = new JwtGrantedAuthoritiesConverter();
        this.scopeAuthoritiesConverter.setAuthorityPrefix("SCOPE_");
        this.scopeAuthoritiesConverter.setAuthoritiesClaimName("scope");
    }

    @Override
    public Collection<GrantedAuthority> convert(Jwt jwt) {
        Collection<GrantedAuthority> scopeAuthorities = scopeAuthoritiesConverter.convert(jwt);
        List<String> roles = jwt.getClaimAsStringList("roles");
        Stream<GrantedAuthority> roleAuthorities = (roles == null ? Stream.<String>empty() : roles.stream())
                .map(role -> (GrantedAuthority) new SimpleGrantedAuthority("ROLE_" + role));
        return Stream.concat(scopeAuthorities.stream(), roleAuthorities).toList();
    }
}
