package com.uptimecrew.tax_liability.llm.cost;

import java.math.BigDecimal;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * {@code modelId} to USD price per 1,000 tokens, priced separately for input and output
 * (W6 D4 Task 2; split from a single blended rate in W6 D5).
 *
 * <p><b>Model ids here are BARE</b> - {@code claude-haiku-4-5}, carrying neither a vendor-prefixed
 * spelling nor a {@code ...-v1:0} suffix. Those two decorations belong to a managed inference
 * gateway's model-id namespace (the CI grep gate for this package rejects either spelling); the
 * direct Anthropic API uses the bare form, and a lookup keyed on the decorated spelling misses
 * and throws rather than silently costing zero - which is the failure mode this class is arranged
 * to avoid.
 *
 * <p><b>A stale price book makes every cost figure wrong while every test still passes.</b>
 * Nothing in this process can detect it: the arithmetic is correct, the log line is well-formed,
 * the header is present, and the number is simply not what the invoice will say. That makes this
 * the highest-maintenance file in the cost package and the one worth re-checking against
 * Anthropic's published pricing whenever a model is added or a rate changes. It is deliberately
 * a small, readable table rather than a remote lookup: a pricing call that can fail is a pricing
 * call that will eventually be made non-blocking, and a non-blocking price lookup defaults to
 * stale silently.
 *
 * <h2>Why the blended rate had to go</h2>
 *
 * <p>Until W6 D5 this table held ONE number per model, blending input and output. The reasoning
 * given for that was explicit and, on its own terms, sound: it keeps the book auditable at a
 * glance, and it is accurate in aggregate for a workload whose input:output ratio is stable.
 *
 * <p>The precondition was stated correctly and then not met, in two separate ways.
 *
 * <p><b>The blend was struck at the wrong ratio.</b> The entry for {@code claude-haiku-4-5} was
 * {@code 0.003}, which is exactly {@code (0.001 + 0.005) / 2} - a 50/50 split of input and output
 * tokens. The real explain-liability call is nothing like 50/50; measured against the live API it
 * is 144 input to 32 output, or 82/18. At that ratio the true cost of a call is $0.000304 and the
 * blend charged $0.000528, so <b>every cost figure this service ever published was 1.74x too
 * high</b>. Nothing detected it, because a blended rate cannot be wrong in a way the arithmetic
 * notices.
 *
 * <p><b>And the precondition stopped holding anyway.</b> A stable ratio is a property of a
 * workload, not of a model, and W6 D4 added {@code POST /v1/completions} - a proxy route that
 * accepts an arbitrary prompt from any caller. The moment that route shipped there was no ratio
 * to be stable: one caller sends a short prompt and asks for an essay, the next pastes a document
 * and asks for a word. A single blended number cannot price both, and the error is silent and
 * unbounded in either direction.
 *
 * <p>So the book now stores the two rates the provider actually bills, and
 * {@link CostMiddleware} multiplies each by its own token count. This is strictly more code and
 * it removes an entire class of quiet mispricing: the only way to be wrong now is to write down
 * a rate that does not match the pricing page, which is a fact a reviewer can check in one look.
 *
 * <h2>Keeping this current</h2>
 *
 * <p>Verify against <a href="https://www.anthropic.com/pricing">Anthropic's pricing page</a>
 * before changing, and re-verify when a model is added. Prices below are per 1,000 tokens,
 * derived from the published per-million rates by dividing by 1,000.
 */
public final class PriceBook {

    /**
     * One model's two rates, in USD per 1,000 tokens.
     *
     * <p>A record rather than two parallel maps: parallel maps can disagree about which models
     * they know, and the failure is a model that prices its input and throws on its output, or
     * worse, prices one side as zero.
     *
     * @param inputPer1K  USD per 1,000 input (prompt) tokens; never null, never negative
     * @param outputPer1K USD per 1,000 output (completion) tokens; never null, never negative
     */
    public record Rates(BigDecimal inputPer1K, BigDecimal outputPer1K) {

        /**
         * @throws NullPointerException     if either rate is null
         * @throws IllegalArgumentException if either rate is negative
         */
        public Rates {
            Objects.requireNonNull(inputPer1K, "inputPer1K must not be null");
            Objects.requireNonNull(outputPer1K, "outputPer1K must not be null");
            if (inputPer1K.signum() < 0 || outputPer1K.signum() < 0) {
                throw new IllegalArgumentException(
                        "rates must not be negative, was in=" + inputPer1K + " out=" + outputPer1K);
            }
        }
    }

    /**
     * The rate table.
     *
     * <p>{@code claude-haiku-4-5} is what {@code explain-liability} runs on, and the gap to Sonnet
     * is the entire argument for that choice: a high-volume, short-answer feature where the larger
     * model's extra capability buys nothing a taxpayer would notice.
     *
     * <p>The Sonnet entry is {@code claude-sonnet-5}, not the {@code claude-sonnet-4-5} that was
     * here before. Sonnet 5 supersedes it and is both more capable and cheaper per token, so
     * keeping the older id would have meant carrying a rate for a model nobody should choose -
     * and this book's entries are, in effect, the menu the proxy route offers.
     */
    private static final Map<String, Rates> RATES = Map.of(
            // Claude Haiku 4.5 - $1.00 / $5.00 per million tokens.
            "claude-haiku-4-5", new Rates(new BigDecimal("0.001"), new BigDecimal("0.005")),
            // Claude Sonnet 5 - $2.00 / $10.00 per million tokens.
            "claude-sonnet-5", new Rates(new BigDecimal("0.002"), new BigDecimal("0.010")));

    private PriceBook() {
        throw new AssertionError("PriceBook is not instantiable");
    }

    /**
     * Look up both rates for a model.
     *
     * @param modelId bare provider model id
     * @return the input and output rates, in USD per 1,000 tokens
     * @throws NullPointerException     if {@code modelId} is null
     * @throws IllegalArgumentException if the model is not in the table. Throwing rather than
     *                                  defaulting to zero is the point: an unpriced model that
     *                                  costs 0.00 produces a cost log and an {@code X-Cost-Usd}
     *                                  header that look perfectly healthy while reporting that a
     *                                  paid call was free. A loud failure at the first call after
     *                                  a model swap is far cheaper than a month of silently
     *                                  under-reported spend.
     */
    public static Rates ratesFor(String modelId) {
        Objects.requireNonNull(modelId, "modelId must not be null");
        Rates rates = RATES.get(modelId);
        if (rates == null) {
            throw new IllegalArgumentException(
                    "no price for model " + modelId + " - known models: " + RATES.keySet()
                            + ". Model ids must be bare (claude-haiku-4-5), with no 'anthropic.' "
                            + "prefix and no Bedrock '-v1:0' suffix.");
        }
        return rates;
    }

    /**
     * Assert a model is priceable, without needing its rates.
     *
     * <p>Exists for {@link com.uptimecrew.tax_liability.llmproxy.LlmProxyController}, which checks
     * that a requested model can be priced <em>before</em> spending money on it. Naming the intent
     * beats calling {@link #ratesFor(String)} and discarding the result, which reads like dead
     * code and invites deletion.
     *
     * @param modelId bare provider model id
     * @throws NullPointerException     if {@code modelId} is null
     * @throws IllegalArgumentException if the model is not in the table
     */
    public static void requirePriceable(String modelId) {
        ratesFor(modelId);
    }

    /** Model ids this price book knows, for diagnostics and tests. */
    public static Set<String> knownModels() {
        return RATES.keySet();
    }
}
