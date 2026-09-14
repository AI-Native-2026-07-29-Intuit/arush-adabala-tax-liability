package com.uptimecrew.tax_liability.llm;

import java.time.Instant;
import java.util.Objects;

import com.uptimecrew.tax_liability.llm.cost.CallContext;
import com.uptimecrew.tax_liability.llm.cost.CostMiddleware;
import com.uptimecrew.tax_liability.llm.cost.UpstreamResponse;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;

import jakarta.servlet.http.HttpServletResponse;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/**
 * The {@code explain-liability} feature (W6 D4 Task 2): a plain-language explanation of why a
 * taxpayer owes what they owe.
 *
 * <p>This is the feature the whole cost plane exists to measure. It is the first thing in this
 * application that spends real money per request, and the spend is invisible to every AWS
 * guardrail built on W6 D3 - Anthropic bills the Anthropic workspace, so no Budget, Cost Explorer
 * report or {@code EstimatedCharges} alarm will ever show it. What makes it governable is the
 * pair this class wires up: {@link CostMiddleware} attributes each call in-app, and an Anthropic
 * Console workspace spend limit caps the total at the platform.
 *
 * <h2>Why Haiku</h2>
 *
 * <p>{@value #MODEL} is roughly a third the price of Sonnet per token and is the right tool for
 * this shape of work: the input is a bounded liability record and the output is a short
 * paragraph, so the extra capability Sonnet brings has nothing to act on. The model is named here
 * rather than taken from {@code spring.ai.anthropic.chat.options.model} precisely so this choice
 * is a per-feature decision visible in the diff - the application default stays Sonnet for the
 * W3 D4 structured-summary path, which does need it.
 *
 * <h2>The explanation is not a calculation</h2>
 *
 * <p>The liability figures in the prompt are already computed, by
 * {@link com.uptimecrew.tax_liability.service.TaxLiabilityService}, in {@code BigDecimal}. The
 * model is asked to explain arithmetic that has already happened, never to perform it. That
 * boundary is the reason this feature can use a cheap model at all, and it is worth stating in
 * the code rather than only in a prompt string: a future prompt change that asks the model for a
 * number would move a tax computation into a component with no test, no audit trail and no
 * determinism guarantee.
 */
@Service
public class LiabilityExplanationService {

    /**
     * Bare Anthropic model id for this feature. Must be a key in
     * {@link com.uptimecrew.tax_liability.llm.cost.PriceBook}, or the first call after a change
     * fails loudly rather than reporting a paid call as free.
     */
    public static final String MODEL = "claude-haiku-4-5";

    /** The {@code feature} dimension value for every call this service makes. */
    public static final String FEATURE = "explain-liability";

    private static final Logger LOG = LoggerFactory.getLogger(LiabilityExplanationService.class);

    private final TaxpayerReadModelRepository readModelRepository;
    private final ChatUpstream upstream;
    private final CostMiddleware costMiddleware;
    private final String service;

    /**
     * @param readModelRepository source of the already-computed liability record; never null
     * @param upstream            the provider adapter; never null
     * @param costMiddleware      the cost path every call is routed through; never null
     * @param service             the {@code service} attribution key, from
     *                            {@code taxcalc.cost.service}; never null
     * @throws NullPointerException if any argument is null
     */
    public LiabilityExplanationService(TaxpayerReadModelRepository readModelRepository,
            ChatUpstream upstream, CostMiddleware costMiddleware,
            @Value("${taxcalc.cost.service:taxcalc}") String service) {
        this.readModelRepository =
                Objects.requireNonNull(readModelRepository, "readModelRepository must not be null");
        this.upstream = Objects.requireNonNull(upstream, "upstream must not be null");
        this.costMiddleware = Objects.requireNonNull(costMiddleware, "costMiddleware must not be null");
        this.service = Objects.requireNonNull(service, "service must not be null");
    }

    /**
     * Explain the given taxpayer's liability in plain language, recording what the call cost.
     *
     * <p>The call is routed through {@link CostMiddleware#observe}, so by the time this returns
     * one EMF cost line has been written and - if {@code response} is non-null - the
     * {@code X-Cost-Usd} header is set. Callers cannot opt out of that: the middleware is on the
     * only path to the model, which is what stops a future feature from spending money without
     * appearing in the cost series.
     *
     * @param id       taxpayer id; never null
     * @param tenant   tenant to bill this call to; never null or blank
     * @param response the servlet response to carry {@code X-Cost-Usd}, or null when called
     *                 outside a request
     * @return the model's explanation text
     * @throws NullPointerException     if {@code id} or {@code tenant} is null
     * @throws IllegalArgumentException if no taxpayer has that id, or {@code tenant} is blank
     * @throws IllegalStateException    if the model returns no text
     */
    public String explain(String id, String tenant, HttpServletResponse response) {
        Objects.requireNonNull(id, "id must not be null");
        TaxpayerReadModel taxpayer = readModelRepository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("unknown id " + id));

        CallContext ctx = new CallContext(Instant.now(), service, tenant, FEATURE, response);
        String prompt = buildPrompt(taxpayer);

        // The lambda is what keeps LlmUpstream prompt-free and provider-agnostic: the prompt and
        // the model id are closed over at the call site, so the middleware never has to know
        // what either looks like.
        UpstreamResponse resp = costMiddleware.observe(ctx, c -> upstream.complete(prompt, MODEL));

        if (resp.text() == null || resp.text().isBlank()) {
            throw new IllegalStateException("explain-liability returned no text for id " + id);
        }
        LOG.info("explain-liability ok id={} tenant={} model={} tokens.in={} tokens.out={}",
                id, tenant, resp.modelId(), resp.inputTokens(), resp.outputTokens());
        return resp.text();
    }

    /**
     * Build the user prompt from an already-computed liability record.
     *
     * <p>Package-private so {@code LiabilityExplanationServiceTest} can assert the two properties
     * that matter without a provider call: that the computed figures reach the model, and that
     * the instruction explicitly forbids recomputing them.
     */
    String buildPrompt(TaxpayerReadModel taxpayer) {
        return """
                You are explaining an already-completed tax calculation to the taxpayer.

                Rules:
                - Do NOT recompute, re-derive or adjust any figure. Every number below is final.
                - Explain in plain language why the liability is what it is.
                - Three sentences at most. No markdown, no bullet points, no preamble.

                Liability record: %s
                """.formatted(taxpayer);
    }
}
