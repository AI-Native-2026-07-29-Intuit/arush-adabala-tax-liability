package com.uptimecrew.tax_liability.llmproxy;

import java.util.Objects;

/**
 * The body of a successful {@code POST /v1/completions} (W6 D4 Task 2).
 *
 * <p><b>The cost is not in here.</b> It travels in the {@code X-Cost-Usd} response header instead,
 * set by {@link com.uptimecrew.tax_liability.llm.cost.CostResponseHeader}. Keeping it in the header
 * means a caller reads the price of a call the same way whatever the body turns out to be - a
 * streamed response, an error envelope, a future body shape - and it is where the W6 D5 k6 cost
 * threshold looks. Putting it in the body as well would give two spellings of one number that can
 * drift apart.
 *
 * @param model         the bare model id that was requested.
 * @param resolvedModel the dated snapshot the provider actually served, e.g.
 *                      {@code claude-haiku-4-5-20251001}. Worth returning: it is the difference
 *                      between "we asked for Haiku" and "Haiku 4.5 of this date answered", which
 *                      is what makes an output reproducible after an alias moves.
 * @param feature       the cost-attribution feature this call was billed to, echoed back so a
 *                      caller can confirm what its spend was labelled.
 * @param inputTokens   prompt tokens, as counted by the provider.
 * @param outputTokens  completion tokens, as counted by the provider.
 * @param text          the completion text.
 */
public record CompletionResponse(
        String model,
        String resolvedModel,
        String feature,
        long inputTokens,
        long outputTokens,
        String text) {

    public CompletionResponse {
        Objects.requireNonNull(model, "model must not be null");
        Objects.requireNonNull(resolvedModel, "resolvedModel must not be null");
        Objects.requireNonNull(feature, "feature must not be null");
    }
}
