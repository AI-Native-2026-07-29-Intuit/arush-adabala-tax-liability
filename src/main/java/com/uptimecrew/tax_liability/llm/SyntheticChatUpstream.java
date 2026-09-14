package com.uptimecrew.tax_liability.llm;

import java.util.Objects;
import java.util.concurrent.ThreadLocalRandom;

import com.uptimecrew.tax_liability.llm.cost.UpstreamResponse;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Primary;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

/**
 * The {@code loadtest} profile's stand-in for {@link AnthropicChatUpstream} (W6 D5 Task 3): same
 * seam, same downstream cost accounting, no provider call and no spend.
 *
 * <h2>Why the k6 gate cannot run against the real provider</h2>
 *
 * <p>The k6 script holds 200 VUs for six minutes against the cost-bearing route. Against
 * Anthropic that is on the order of a hundred thousand paid completions per run, on every pull
 * request that touches {@code loadtests/**} - which is both a real bill and, long before the
 * bill, a rate-limit wall. This repository has already been bitten by the second one: a capped
 * key is why several W6 D4 tests skip themselves rather than fail.
 *
 * <p>There is also a measurement reason, and it is the stronger one. The W5 D5 SLO is
 * {@code p99 <= 500 ms}. A real Haiku completion takes one to three seconds, so a load test
 * against the live provider measures Anthropic's queue, not this service's, and could never meet
 * that objective no matter how well {@code taxcalc-api} scaled. The HPA under test scales on
 * in-flight requests <em>held by this service</em>; the number the gate must protect is this
 * service's own serving latency.
 *
 * <h2>What is real here and what is not</h2>
 *
 * <p><b>Real:</b> the cost arithmetic. This class returns token counts and a model id, and
 * everything after that - {@link com.uptimecrew.tax_liability.llm.cost.PriceBook} lookup,
 * {@link com.uptimecrew.tax_liability.llm.cost.CostMiddleware}'s {@code BigDecimal} division and
 * single HALF_UP rounding, the EMF cost log, the {@code X-Cost-Usd} header - is the same code
 * that runs in production, unaware it was not Anthropic on the other end. So the header the k6
 * {@code cost_per_request_usd} threshold reads is genuine arithmetic over the real price book: if
 * someone mis-prices a model, widens the token budget, or reintroduces the {@code Double.toString}
 * bug, the gate moves. That is what makes the threshold able to fail rather than decorative.
 *
 * <p><b>Not real:</b> the token counts, and therefore the absolute dollar figure. They are drawn
 * from a band that matches what this feature actually bills in production - measured from the W6
 * D4 cost logs - but they are synthetic, and a cost regression caused by <em>prompts getting
 * longer</em> is invisible to this gate. Latency is likewise this service's own, with no provider
 * hop in it. Neither number is evidence about Anthropic.
 *
 * <p>The profile is not active anywhere but a load test. With it off, the bean does not exist and
 * {@link AnthropicChatUpstream} is the only {@link ChatUpstream} in the context - so there is no
 * configuration under which production traffic can be served a fake completion by accident. The
 * one-line startup warning below exists so that if this profile is ever switched on somewhere it
 * should not be, the reason a cost dashboard has gone quiet is the first thing in the log.
 */
@Component
@Profile("loadtest")
@Primary
public class SyntheticChatUpstream implements ChatUpstream {

    private static final Logger LOG = LoggerFactory.getLogger(SyntheticChatUpstream.class);

    /**
     * Canned completion text. Length is in the range a real explain-liability answer occupies, so
     * response serialisation and gzip behave the way they do in production rather than on a
     * two-word string.
     */
    private static final String TEXT =
            "Your estimated liability is driven mainly by ordinary income taxed across the first "
                    + "three brackets, with the standard deduction applied before the bracket walk. "
                    + "State liability is computed separately against your home jurisdiction's flat "
                    + "rate and added to the federal total.";

    private final int minInputTokens;
    private final int maxInputTokens;
    private final int minOutputTokens;
    private final int maxOutputTokens;

    /**
     * Token bands, configurable so a load test can deliberately push the cost gate over its
     * threshold and confirm the gate fails - a threshold nobody has ever seen go red is a
     * threshold nobody knows is wired up.
     *
     * <p>The defaults bracket what W6 D4's cost logs show a real {@code explain-liability} call
     * billing: roughly 110-160 prompt tokens and 30-60 completion tokens.
     *
     * @param minInputTokens  lower bound of the prompt-token band; must be >= 0
     * @param maxInputTokens  upper bound of the prompt-token band; must be >= minInputTokens
     * @param minOutputTokens lower bound of the completion-token band; must be >= 0
     * @param maxOutputTokens upper bound of the completion-token band; must be >= minOutputTokens
     * @throws IllegalArgumentException if any bound is negative or a max is below its min
     */
    public SyntheticChatUpstream(
            @Value("${taxcalc.loadtest.tokens.input.min:110}") int minInputTokens,
            @Value("${taxcalc.loadtest.tokens.input.max:160}") int maxInputTokens,
            @Value("${taxcalc.loadtest.tokens.output.min:30}") int minOutputTokens,
            @Value("${taxcalc.loadtest.tokens.output.max:60}") int maxOutputTokens) {
        requireBand(minInputTokens, maxInputTokens, "input");
        requireBand(minOutputTokens, maxOutputTokens, "output");
        this.minInputTokens = minInputTokens;
        this.maxInputTokens = maxInputTokens;
        this.minOutputTokens = minOutputTokens;
        this.maxOutputTokens = maxOutputTokens;
        LOG.warn("SYNTHETIC LLM UPSTREAM ACTIVE (profile=loadtest): no provider call is being made "
                + "and no Anthropic spend is being incurred. X-Cost-Usd is real arithmetic over "
                + "synthetic token counts. This must never be active in production.");
    }

    /**
     * Return a completion without calling a provider.
     *
     * <p>Argument validation is identical to {@link AnthropicChatUpstream}'s, deliberately: a load
     * test that accepted a blank model id where production rejects it would hide a caller bug
     * until the day the profile is off.
     *
     * <p>The {@code resolvedModelId} is the requested id rather than a fabricated snapshot. W6 D4
     * established that the alias is the pricing key and the snapshot is only for invoice
     * reconciliation; inventing a snapshot here would put a model id in the cost log that no
     * invoice will ever contain.
     *
     * @param prompt  the user prompt; never null or blank
     * @param modelId bare provider model id; never null or blank
     * @return a synthetic response with token counts drawn from the configured bands
     * @throws NullPointerException     if either argument is null
     * @throws IllegalArgumentException if either argument is blank
     */
    @Override
    public UpstreamResponse complete(String prompt, String modelId) {
        requireText(prompt, "prompt");
        requireText(modelId, "modelId");

        long inputTokens = between(minInputTokens, maxInputTokens);
        long outputTokens = between(minOutputTokens, maxOutputTokens);

        // latencyMs is 0, not a fabricated provider latency. A made-up number here would flow
        // into the cost log's latency field and be indistinguishable from a measurement; 0 is
        // obviously synthetic to anyone reading the series.
        return new UpstreamResponse(modelId, modelId, inputTokens, outputTokens, 0L, true, TEXT);
    }

    /**
     * Inclusive random draw.
     *
     * @param min lower bound, inclusive
     * @param max upper bound, inclusive
     * @return a value in {@code [min, max]}
     */
    private static long between(int min, int max) {
        return min == max ? min : ThreadLocalRandom.current().nextInt(min, max + 1);
    }

    private static void requireBand(int min, int max, String field) {
        if (min < 0) {
            throw new IllegalArgumentException(field + " token minimum must not be negative, was " + min);
        }
        if (max < min) {
            throw new IllegalArgumentException(
                    field + " token maximum (" + max + ") must not be below its minimum (" + min + ")");
        }
    }

    private static void requireText(String value, String field) {
        Objects.requireNonNull(value, field + " must not be null");
        if (value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
    }

    @Override
    public String toString() {
        return "SyntheticChatUpstream{input=" + minInputTokens + ".." + maxInputTokens
                + ", output=" + minOutputTokens + ".." + maxOutputTokens + "}";
    }
}
