package com.uptimecrew.tax_liability.orders;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Requires a bearer credential and a tenant selector on every business endpoint.
 *
 * <p><strong>What this verifies, and what it explicitly does not.</strong> It checks that an
 * {@code Authorization: Bearer ...} header and an {@code X-Tenant} header are present and
 * well-formed. It does <em>not</em> verify the token's signature, issuer, audience or scopes.
 *
 * <p>That limit is deliberate and worth stating plainly rather than leaving for someone to
 * discover: this service exists so an end-to-end test can prove that the MCP server forwards the
 * caller's credential and tenant on every outbound call, and signature verification needs an
 * identity provider in the test topology to issue tokens against. Presence is exactly the
 * property the test is asserting, and it is the property that regresses - a refactor that drops
 * the {@code Authorization} header is caught here, and no amount of signature checking would
 * catch more of it.
 *
 * <p><strong>Consequently this service must not be deployed as a trust boundary.</strong> In the
 * real topology the token is verified by the service that owns the data, against the real issuer.
 *
 * <p>The health endpoint is exempt, because a readiness probe that needed a credential would
 * make orchestration depend on the identity provider being up.
 */
@Component
@Order(1)
public final class TenantAuthFilter extends OncePerRequestFilter {

    /** Scheme prefix, compared case-insensitively because RFC 7235 says the scheme is. */
    private static final String BEARER_PREFIX = "bearer ";

    /** Selects which of the tenants the credential may reach this call is for. */
    private static final String TENANT_HEADER = "X-Tenant";

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        // Actuator only. Everything else, including any path added later, is filtered by
        // default - the safe direction for this list to be wrong in.
        return request.getRequestURI().startsWith("/actuator");
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {

        String authorization = request.getHeader("Authorization");
        if (authorization == null || !authorization.toLowerCase().startsWith(BEARER_PREFIX)) {
            reject(response, "missing or malformed Authorization bearer header");
            return;
        }
        if (authorization.substring(BEARER_PREFIX.length()).isBlank()) {
            reject(response, "bearer token is empty");
            return;
        }
        String tenant = request.getHeader(TENANT_HEADER);
        if (tenant == null || tenant.isBlank()) {
            reject(response, "missing " + TENANT_HEADER + " header");
            return;
        }
        chain.doFilter(request, response);
    }

    /**
     * Writes a 401 whose body says what was wrong with the credential, and nothing more.
     *
     * @param response the response to write
     * @param message the caller-facing reason
     * @throws IOException if the response cannot be written
     */
    private static void reject(HttpServletResponse response, String message) throws IOException {
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType("application/json");
        response.getWriter().write("{\"error\":\"" + message + "\"}");
    }
}
