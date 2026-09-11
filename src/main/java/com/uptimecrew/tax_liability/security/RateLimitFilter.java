package com.uptimecrew.tax_liability.security;

import java.io.IOException;
import java.time.Duration;
import java.util.List;
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
 * Caps every authenticated caller at 10 requests/minute against the LLM-facing routes
 * ({@code /summary} and, from W6 D4, {@code /explanation}): LLM tokens cost money, so a single
 * misbehaving client must not be able to torch the monthly budget.
 *
 * <p>W6 D4 note - this filter is a genuine cost control and not merely an abuse control, and
 * {@code /explanation} is the route that makes the difference concrete. It is the first endpoint
 * here that spends real money on every call, and the spend is invisible to every AWS guardrail
 * the W6 D3 substrate carries, because Anthropic bills the Anthropic workspace rather than AWS.
 * Between the Anthropic Console workspace spend limit (the hard cap) and
 * {@link com.uptimecrew.tax_liability.llm.cost.CostLogger} (the attribution), this filter is the
 * part that bounds the worst case <em>before</em> the money is spent - a retry storm against an
 * LLM endpoint is the failure mode most likely to produce a surprising invoice, and a platform
 * cap only stops it after the fact.
 *
 * <p>Buckets are keyed by JWT {@code sub} so each caller has its own budget, and the
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

    /**
     * The route suffixes that reach a paid model. A route added to the application without being
     * added here spends money outside this cap silently, so the list is kept next to the check
     * rather than inlined into it - and {@code RateLimitFilterTest} asserts every entry is
     * covered, so adding one without a test is a failure rather than an omission.
     */
    private static final List<String> LLM_ROUTE_SUFFIXES = List.of("/summary", "/explanation");

    private final ConcurrentMap<String, Bucket> bucketsBySubject = new ConcurrentHashMap<>();

    /** Whether this URI reaches a paid model and must therefore be rate limited. */
    /**
     * The LLM proxy route (W6 D4 Task 2). Matched exactly rather than by suffix: it sits outside
     * the {@code /api/} tree, and it is the one route whose entire purpose is to spend money, so
     * leaving it off this list would leave the cheapest path to a surprising invoice unmetered.
     */
    private static final String LLM_PROXY_ROUTE = "/v1/completions";

    static boolean isLlmRoute(String uri) {
        if (uri == null) {
            return false;
        }
        if (LLM_PROXY_ROUTE.equals(uri)) {
            return true;
        }
        if (!uri.startsWith("/api/")) {
            return false;
        }
        return LLM_ROUTE_SUFFIXES.stream().anyMatch(uri::endsWith);
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String uri = request.getRequestURI();
        if (!isLlmRoute(uri)) {
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
