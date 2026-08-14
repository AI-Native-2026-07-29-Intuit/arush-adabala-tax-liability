package com.uptimecrew.tax_liability.security;

import java.io.IOException;
import java.time.Duration;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

import io.github.bucket4j.Bandwidth;
import io.github.bucket4j.Bucket;
import io.github.bucket4j.Refill;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Caps every authenticated caller at 10 requests/minute against the LLM-facing {@code /summary}
 * route: LLM tokens cost money, so a single misbehaving client must not be able to torch the
 * monthly budget. Buckets are keyed by JWT {@code sub} so each caller has its own budget, and the
 * filter is registered after {@link
 * org.springframework.security.oauth2.server.resource.web.authentication.BearerTokenAuthenticationFilter}
 * (see {@link SecurityConfig}) so the JWT principal is already resolved when the bucket lookup
 * runs.
 *
 * <p>The store is an in-memory {@link ConcurrentHashMap} for this deliverable: it is fine for a
 * single instance, but a multi-instance deployment needs the limit shared across instances, which
 * is exactly what {@code bucket4j-redis} (already on the classpath, see {@code build.gradle})
 * provides — one Redis round-trip per request instead of a limit that resets on restart and does
 * not coordinate across pods.
 */
@Component
public final class RateLimitFilter extends OncePerRequestFilter {

    private static final int REQUESTS_PER_MINUTE = 10;
    private static final int STATUS_TOO_MANY_REQUESTS = 429;
    private static final String RETRY_AFTER_SECONDS = "60";

    private final ConcurrentMap<String, Bucket> bucketsBySubject = new ConcurrentHashMap<>();

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String uri = request.getRequestURI();
        if (!uri.startsWith("/api/") || !uri.endsWith("/summary")) {
            chain.doFilter(request, response);
            return;
        }

        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (!(auth instanceof JwtAuthenticationToken jwtAuth)) {
            // Filter chain ordering: the bearer-token filter has already rejected anonymous
            // /api/** calls; reaching here means a JWT is present.
            chain.doFilter(request, response);
            return;
        }

        Jwt jwt = jwtAuth.getToken();
        String subject = jwt.getSubject();
        Bucket bucket = bucketsBySubject.computeIfAbsent(subject, s -> Bucket.builder()
                .addLimit(Bandwidth.classic(REQUESTS_PER_MINUTE, Refill.intervally(REQUESTS_PER_MINUTE, Duration.ofMinutes(1))))
                .build());

        if (bucket.tryConsume(1)) {
            chain.doFilter(request, response);
        } else {
            response.setStatus(STATUS_TOO_MANY_REQUESTS);
            response.setHeader("Retry-After", RETRY_AFTER_SECONDS);
            response.setContentType("application/json");
            response.getWriter().write("{\"error\":\"rate_limited\"}");
        }
    }
}
