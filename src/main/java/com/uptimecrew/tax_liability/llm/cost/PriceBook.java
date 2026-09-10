package com.uptimecrew.tax_liability.llm.cost;

import java.math.BigDecimal;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * {@code modelId} to USD price per 1,000 tokens (W6 D4 Task 2).
 *
 * <p><b>Model ids here are BARE</b> - {@code claude-haiku-4-5}, not {@code anthropic.claude-...}
 * and not {@code ...-v1:0}. Those two decorations belong to Bedrock's model-id namespace; the
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
 * <p><b>Blended rate, and why.</b> One rate covers input and output tokens together, and real
 * provider pricing charges output several times more than input. The blend is therefore an
 * approximation, deliberately taken and worth naming: it keeps the price book one number per
 * model, which is what makes it auditable at a glance. It is accurate in aggregate for a
 * workload whose input:output ratio is stable - which explain-liability's is, since it sends a
 * bounded liability record and asks for a short paragraph. It would be the wrong simplification
 * for a summarisation workload with a huge prompt and a one-word answer, and
 * {@link #priceFor(String)} would need to split into input/output rates before serving one.
 */
public final class PriceBook {

    /**
     * USD per 1,000 tokens, blended input+output.
     *
     * <p>Verify against <a href="https://www.anthropic.com/pricing">Anthropic's pricing page</a>
     * before changing. The two entries differ by 3x, which is the entire argument for
     * explain-liability using Haiku: it is a high-volume, short-answer feature where Sonnet's
     * extra capability buys nothing a taxpayer would notice.
     */
    private static final Map<String, BigDecimal> PER_1K = Map.of(
            "claude-haiku-4-5", new BigDecimal("0.003"),
            "claude-sonnet-4-5", new BigDecimal("0.009"));

    private PriceBook() {
        throw new AssertionError("PriceBook is not instantiable");
    }

    /**
     * @param modelId bare provider model id
     * @return USD per 1,000 tokens for that model
     * @throws NullPointerException     if {@code modelId} is null
     * @throws IllegalArgumentException if the model is not in the table. Throwing rather than
     *                                  defaulting to zero is the point: an unpriced model that
     *                                  costs 0.00 produces a cost log and an {@code X-Cost-Usd}
     *                                  header that look perfectly healthy while reporting that a
     *                                  paid call was free. A loud failure at the first call after
     *                                  a model swap is far cheaper than a month of silently
     *                                  under-reported spend.
     */
    public static BigDecimal priceFor(String modelId) {
        Objects.requireNonNull(modelId, "modelId must not be null");
        BigDecimal price = PER_1K.get(modelId);
        if (price == null) {
            throw new IllegalArgumentException(
                    "no price for model " + modelId + " - known models: " + PER_1K.keySet()
                            + ". Model ids must be bare (claude-haiku-4-5), with no 'anthropic.' "
                            + "prefix and no Bedrock '-v1:0' suffix.");
        }
        return price;
    }

    /** Model ids this price book knows, for diagnostics and tests. */
    public static Set<String> knownModels() {
        return PER_1K.keySet();
    }
}
