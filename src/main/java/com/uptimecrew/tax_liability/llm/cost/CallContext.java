package com.uptimecrew.tax_liability.llm.cost;

import java.time.Instant;
import java.util.Objects;

import jakarta.servlet.http.HttpServletResponse;

/**
 * The attribution keys for one LLM call, plus the response the {@code X-Cost-Usd} header is
 * written to (W6 D4 Task 2).
 *
 * <p><b>This type deliberately names no provider.</b> It carries who is asking ({@code tenant}),
 * what for ({@code feature}), and under which service ({@code service}) - three facts that stay
 * true whichever upstream ends up serving the request. Swapping the direct Anthropic API for a
 * managed inference gateway (Bedrock {@code InvokeModel}, say) changes {@link LlmUpstream}'s
 * implementation and nothing here, which is what makes the provider choice a runtime config
 * change rather than a code change.
 *
 * <p>The three attribution fields become the CloudWatch EMF dimension set
 * {@code [[service, tenant, feature]]} in {@link CostLogger}, so they are also the axes any
 * per-feature or per-tenant spend query can group by. A field added here without a matching
 * dimension there is invisible to that query; the two are kept in step on purpose.
 *
 * @param at       when the call started. Passed in rather than read from the clock inside
 *                 {@link CostMiddleware} so a test can assert on an exact timestamp without
 *                 stubbing time.
 * @param service  the billing service name, e.g. {@code taxcalc}. Matches the {@code service}
 *                 cost-allocation tag on the AWS plane, so the two planes' spend views key on
 *                 the same string even though nothing joins them automatically.
 * @param tenant   the tenant this call is billed to. {@code shared} where no tenant is resolved.
 * @param feature  the product feature making the call, e.g. {@code explain-liability}. This is
 *                 the key the AWS Budget cannot supply: Anthropic spend never reaches AWS
 *                 billing, so per-feature LLM attribution exists only because it is recorded
 *                 here.
 * @param response the servlet response to attach {@code X-Cost-Usd} to. Nullable - a call made
 *                 outside a request (a scheduled job, a warm-up) still gets costed and logged,
 *                 it simply has no response to carry the header.
 */
public record CallContext(
        Instant at,
        String service,
        String tenant,
        String feature,
        HttpServletResponse response) {

    /**
     * @throws NullPointerException     if any of {@code at}, {@code service}, {@code tenant} or
     *                                  {@code feature} is null ({@code response} may be null)
     * @throws IllegalArgumentException if {@code service}, {@code tenant} or {@code feature} is
     *                                  blank - a blank attribution key is worse than a missing
     *                                  one, because it produces a real EMF dimension whose value
     *                                  is the empty string and silently splits the cost series
     *                                  in two
     */
    public CallContext {
        Objects.requireNonNull(at, "at must not be null");
        requireText(service, "service");
        requireText(tenant, "tenant");
        requireText(feature, "feature");
    }

    private static void requireText(String value, String field) {
        Objects.requireNonNull(value, field + " must not be null");
        if (value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
    }

    /** A context with no servlet response - for calls made outside an HTTP request. */
    public static CallContext detached(Instant at, String service, String tenant, String feature) {
        return new CallContext(at, service, tenant, feature, null);
    }
}
