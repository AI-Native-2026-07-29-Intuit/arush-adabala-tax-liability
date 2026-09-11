package com.uptimecrew.tax_liability.llmproxy;

import java.util.Objects;

import com.uptimecrew.tax_liability.llm.LiabilityExplanationService;

/**
 * The body of a {@code POST /v1/completions} call (W6 D4 Task 2).
 *
 * <p><b>{@code feature} is here; {@code tenant} deliberately is not.</b> The two are both cost
 * attribution dimensions, and they are sourced differently on purpose. A feature name is a label
 * the calling code applies to its own spend - the caller is the only thing that knows whether this
 * call is {@code explain-liability} or something else, and mislabelling it only mis-sorts the
 * caller's own line. A tenant is a billing identity, so it comes from the verified JWT claim in
 * {@link LlmProxyController} and never from the body: a caller-supplied tenant on a cost key is a
 * caller who can bill their spend to somebody else's line.
 *
 * @param prompt  the user prompt to send upstream. Required and non-blank.
 * @param model   bare model id, e.g. {@code claude-haiku-4-5}. Optional - defaults to
 *                {@link LiabilityExplanationService#MODEL}. Must be one the
 *                {@link com.uptimecrew.tax_liability.llm.cost.PriceBook} can price, which the
 *                controller checks BEFORE spending anything upstream.
 * @param feature the cost-attribution feature name, e.g. {@code explain-liability}. Required and
 *                non-blank, because
 *                {@link com.uptimecrew.tax_liability.llm.cost.CallContext} rejects a blank
 *                dimension value - a blank one silently splits the CloudWatch series in two
 *                rather than failing.
 */
public record CompletionRequest(String prompt, String model, String feature) {

    public CompletionRequest {
        requireText(prompt, "prompt");
        requireText(feature, "feature");
        if (model == null || model.isBlank()) {
            model = LiabilityExplanationService.MODEL;
        }
    }

    private static void requireText(String value, String field) {
        Objects.requireNonNull(value, field + " must not be null");
        if (value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
    }
}
