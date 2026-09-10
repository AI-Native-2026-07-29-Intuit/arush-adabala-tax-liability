package com.uptimecrew.tax_liability.llm.cost;

import java.util.Objects;

/**
 * What one LLM call returned, reduced to the facts cost accounting needs (W6 D4 Task 2).
 *
 * <p>Token counts come from the provider's own usage block ({@code usage.input_tokens} /
 * {@code usage.output_tokens} on the Anthropic API; {@code ChatResponse.getMetadata().getUsage()}
 * as Spring AI surfaces it here) and are never estimated from the prompt. An estimate would be
 * wrong in the one direction that matters - it cannot see the tokens the model actually
 * generated - and a cost figure that drifts from the invoice is worse than none, because it gets
 * trusted.
 *
 * <h2>Two model ids, and why both are needed</h2>
 *
 * <p>A real call to the Anthropic API with {@code model: "claude-haiku-4-5"} comes back reporting
 * {@code "model": "claude-haiku-4-5-20251001"}. The requested id is a floating <em>alias</em>; the
 * response names the dated <em>snapshot</em> that actually served it. Measured against the live
 * API, not inferred from the docs.
 *
 * <p>That matters twice over:
 *
 * <ul>
 *   <li><b>Price by the alias.</b> {@link PriceBook} is keyed on the alias, so looking a price up
 *       by the response's snapshot id would throw {@code no price for model
 *       claude-haiku-4-5-20251001} on the first real call - a failure that unit tests using a
 *       stubbed provider will never produce, because a stub echoes back whatever it was asked
 *       for.
 *   <li><b>Log the snapshot.</b> An alias floats: {@code claude-haiku-4-5} will point at a
 *       different snapshot later, possibly at a different rate. A cost log that records only the
 *       alias cannot be reconciled against an invoice line afterwards, because it does not say
 *       what was actually billed.
 * </ul>
 *
 * @param modelId         the model id as REQUESTED - bare ({@code claude-haiku-4-5}), no
 *                        {@code anthropic.} prefix, no Bedrock {@code -v1:0} suffix. This is the
 *                        pricing key.
 * @param resolvedModelId the model id the provider REPORTS having served the call with, e.g.
 *                        {@code claude-haiku-4-5-20251001}. Audit only, never priced. Falls back
 *                        to {@code modelId} where a provider reports nothing.
 * @param inputTokens     prompt tokens billed
 * @param outputTokens    completion tokens billed
 * @param latencyMs       wall-clock duration of the upstream call
 * @param success         whether the call succeeded. A failed call can still have billed tokens
 *                        (a completion cut short by a filter, say), so cost is recorded either
 *                        way and success travels as a field rather than as a reason to skip
 *                        logging.
 * @param text            the assistant's text, or null if the call failed. Not used for costing;
 *                        carried so {@link CostMiddleware} can stay on the return path.
 */
public record UpstreamResponse(
        String modelId,
        String resolvedModelId,
        long inputTokens,
        long outputTokens,
        long latencyMs,
        boolean success,
        String text) {

    /**
     * @throws NullPointerException     if {@code modelId} is null
     * @throws IllegalArgumentException if {@code modelId} is blank, or if any of the three
     *                                  numeric fields is negative. Negative tokens are not a
     *                                  hypothetical: a provider returning null usage gets
     *                                  defaulted to 0 by the adapter, and a subtraction bug in
     *                                  that defaulting would otherwise produce a negative cost
     *                                  that quietly reduces the running total.
     */
    public UpstreamResponse {
        Objects.requireNonNull(modelId, "modelId must not be null");
        if (modelId.isBlank()) {
            throw new IllegalArgumentException("modelId must not be blank");
        }
        // Defaulted rather than required: a provider that reports no model id should degrade the
        // audit trail, not fail a call the taxpayer is waiting on.
        if (resolvedModelId == null || resolvedModelId.isBlank()) {
            resolvedModelId = modelId;
        }
        requireNonNegative(inputTokens, "inputTokens");
        requireNonNegative(outputTokens, "outputTokens");
        requireNonNegative(latencyMs, "latencyMs");
    }

    /**
     * Convenience for callers and tests that do not distinguish alias from snapshot.
     *
     * @param modelId the requested (and therefore priced) model id
     */
    public static UpstreamResponse of(String modelId, long inputTokens, long outputTokens,
            long latencyMs, boolean success, String text) {
        return new UpstreamResponse(modelId, modelId, inputTokens, outputTokens, latencyMs, success, text);
    }

    private static void requireNonNegative(long value, String field) {
        if (value < 0) {
            throw new IllegalArgumentException(field + " must not be negative, was " + value);
        }
    }

    /** Total billed tokens. */
    public long totalTokens() {
        return inputTokens + outputTokens;
    }

    /** Whether the provider served this call with a different (dated) model than was requested. */
    public boolean servedByDifferentSnapshot() {
        return !modelId.equals(resolvedModelId);
    }
}
