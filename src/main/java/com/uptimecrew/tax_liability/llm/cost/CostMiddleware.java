package com.uptimecrew.tax_liability.llm.cost;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Objects;

/**
 * Wraps one LLM call: invoke the model, compute what it cost, write the structured cost log, set
 * the {@code X-Cost-Usd} header (W6 D4 Task 2).
 *
 * <p>Cost comes from the provider's own response usage. There is no Redis, no running total, and
 * no in-app kill switch, and that is a deliberate division of labour rather than an omission:
 *
 * <ul>
 *   <li><b>Capped at the platform.</b> The hard limit on Anthropic spend is the Anthropic Console
 *       workspace spend limit. It is enforced by the party doing the billing, it cannot be
 *       bypassed by a bug in this process, and it survives this application being scaled to N
 *       replicas - none of which is true of a counter the application keeps for itself.
 *   <li><b>Attributed in-app.</b> What the platform cap cannot do is say <em>which feature</em>
 *       spent the money, because Anthropic bills a workspace and knows nothing about
 *       {@code explain-liability}. That is what {@link CostLogger} exists for.
 * </ul>
 *
 * <p>An in-app kill switch would be the worst of both: per-replica state that under-counts by a
 * factor of the replica count, and a new failure mode (the cost store being down) on the request
 * path of a feature that is meant to degrade gracefully.
 *
 * <h2>Why cost is a {@code long} of 1e-5 USD and not a scale-2 {@link BigDecimal}</h2>
 *
 * <p>This is a deliberate, documented departure from the project convention in {@code CLAUDE.md}
 * that monetary values are {@code BigDecimal} at scale 2, HALF_UP. That rule exists for tax
 * liability amounts, where scale 2 <em>is</em> the domain: dollars and cents, and a rounding
 * error is unacceptable. Per-request LLM cost lives four orders of magnitude below that - a Haiku
 * explain-liability call costs around $0.0002 - so at scale 2 every call rounds to {@code 0.00}
 * and the monthly total of a million calls rounds to zero too. Applying the convention literally
 * here would not be conservative; it would delete the measurement.
 *
 * <p>The convention's actual intent - never accumulate money in binary floating point - is kept,
 * and arguably kept harder. The computation runs in {@link BigDecimal} at scale 8, is rounded
 * exactly once with HALF_UP, and is then carried as an integer count of 1e-5 USD. Integers add
 * without error, so a sum over a month is exact where a chain of scale-2 roundings would not be.
 * {@link #longValueExact()} rather than {@code longValue()} means an overflow throws instead of
 * wrapping to a negative cost.
 */
public class CostMiddleware {

    /**
     * Working scale for the division. Well below the 1e-5 unit the result is quantised to, so the
     * single HALF_UP rounding at the end is the only one that happens - dividing straight to
     * scale 5 would round twice for models priced with more precision.
     */
    private static final int WORKING_SCALE = 8;

    private static final BigDecimal TOKENS_PER_PRICE_UNIT = BigDecimal.valueOf(1000);

    private final CostLogger log;

    /**
     * @param log the cost logger to emit through; never null
     * @throws NullPointerException if {@code log} is null
     */
    public CostMiddleware(CostLogger log) {
        this.log = Objects.requireNonNull(log, "log must not be null");
    }

    /**
     * Invoke the upstream and record what it cost.
     *
     * <p>Ordering matters and is asserted in {@code CostMiddlewareTest}: the cost log is written
     * <em>before</em> the header is attached. A header write on an already-committed response
     * throws or is silently dropped by the container, and if that happened first the call would
     * be lost from the cost series entirely. This way the worst case is a missing header on a
     * response that was already on its way out, with the spend still recorded.
     *
     * @param ctx      attribution context; never null
     * @param upstream the provider boundary to call; never null
     * @return the upstream's response, unchanged
     * @throws NullPointerException     if {@code ctx} or {@code upstream} is null
     * @throws IllegalArgumentException if the response names a model {@link PriceBook} has no
     *                                  price for
     * @throws ArithmeticException      if the computed cost overflows a {@code long} - which at
     *                                  1e-5 USD per unit means a single call costing more than
     *                                  ~$92 trillion, i.e. a corrupt token count rather than a
     *                                  real charge
     */
    public UpstreamResponse observe(CallContext ctx, LlmUpstream upstream) {
        Objects.requireNonNull(ctx, "ctx must not be null");
        Objects.requireNonNull(upstream, "upstream must not be null");

        UpstreamResponse resp = upstream.complete(ctx);
        Objects.requireNonNull(resp, "upstream returned a null response");

        long costUsdE5 = costUsdE5(resp);
        log.emit(ctx, resp, costUsdE5);
        CostResponseHeader.attach(ctx.response(), costUsdE5);
        return resp;
    }

    /**
     * Cost of one call in integer units of 1e-5 USD.
     *
     * <p>Package-private and separately tested so the arithmetic can be asserted against
     * hand-computed values without a servlet or a stub upstream.
     *
     * @param resp the completed call; never null
     * @return cost in integer 1e-5 USD units, never negative
     */
    long costUsdE5(UpstreamResponse resp) {
        Objects.requireNonNull(resp, "resp must not be null");
        BigDecimal pricePerK = PriceBook.priceFor(resp.modelId());
        BigDecimal costUsd = pricePerK
                .multiply(BigDecimal.valueOf(resp.totalTokens()))
                .divide(TOKENS_PER_PRICE_UNIT, WORKING_SCALE, RoundingMode.HALF_UP);
        return costUsd
                .setScale(CostResponseHeader.COST_SCALE, RoundingMode.HALF_UP)
                .movePointRight(CostResponseHeader.COST_SCALE)
                .longValueExact();
    }
}
