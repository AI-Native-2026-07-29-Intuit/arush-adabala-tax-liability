package com.uptimecrew.tax_liability.llm.cost;

/**
 * The provider boundary (W6 D4 Task 2). One method: make the call, report what it cost in tokens.
 *
 * <p>The interface is named for what it is to this application - an upstream language model - and
 * not for who serves it. {@link CostMiddleware} is written against this type alone, so the entire
 * cost path (pricing, the structured log, the {@code X-Cost-Usd} header) is untouched by a change
 * of provider. That is the property the W3 D1 proxy boundary was introduced for, and it is worth
 * restating here because this is the layer where it is easiest to lose: a middleware that reached
 * for an Anthropic-specific response type would re-couple the two in one line.
 *
 * <p>Concretely, swapping the direct Anthropic API for a managed inference gateway (Bedrock
 * {@code InvokeModel}) means a new implementation of this interface and a config change naming
 * it. The request/response shape, the token-based accounting and the per-feature attribution are
 * identical on either side - only "who runs the model" differs.
 *
 * <p>Implementations are expected to translate provider failures into an
 * {@link UpstreamResponse} with {@code success=false} where token usage is still known, and to
 * throw otherwise. {@link CostMiddleware} costs and logs both paths.
 */
@FunctionalInterface
public interface LlmUpstream {

    /**
     * Invoke the model for the given call.
     *
     * @param ctx attribution and timing context for this call; never null
     * @return what the call returned and what it billed; never null
     */
    UpstreamResponse complete(CallContext ctx);
}
