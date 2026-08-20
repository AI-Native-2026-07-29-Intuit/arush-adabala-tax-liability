package com.uptimecrew.tax_liability.graphql;

import java.util.List;
import java.util.Map;
import java.util.Objects;

import com.uptimecrew.tax_liability.llm.LlmSummaryService;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.service.TaxLiabilityService;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.graphql.data.method.annotation.Argument;
import org.springframework.graphql.data.method.annotation.BatchMapping;
import org.springframework.graphql.data.method.annotation.MutationMapping;
import org.springframework.graphql.data.method.annotation.QueryMapping;
import org.springframework.stereotype.Controller;

/**
 * Exposes {@link TaxLiabilityService} over {@code schema.graphqls} (W3 D4). {@code @QueryMapping}
 * / {@code @MutationMapping} bind to top-level {@code Query} / {@code Mutation} fields by method
 * name; {@code @BatchMapping} resolves {@code Taxpayer.lines} once per generation instead of once
 * per taxpayer, fixing the N+1 that a naive per-parent resolver would otherwise cause.
 *
 * <p>{@link #taxpayersByTag} (W3 D5) is the small feature shipped through the 3-agent
 * generator/tester/reviewer workflow that day: filters the Mongo read model by the new
 * {@code Taxpayer.tags} field via {@link TaxLiabilityService#findByTag}.
 */
@Controller
public class TaxpayerGraphQlController {

    private static final Logger LOG = LoggerFactory.getLogger(TaxpayerGraphQlController.class);

    private final TaxLiabilityService service;
    private final LlmSummaryService llmSummary;

    public TaxpayerGraphQlController(TaxLiabilityService service, LlmSummaryService llmSummary) {
        this.service = Objects.requireNonNull(service, "service must not be null");
        this.llmSummary = Objects.requireNonNull(llmSummary, "llmSummary must not be null");
    }

    @QueryMapping
    public TaxpayerReadModel taxpayer(@Argument String id) {
        LOG.info("graphql query taxpayer id={}", id);
        return service.findById(id).orElse(null);
    }

    @QueryMapping
    public List<TaxpayerReadModel> latestTaxpayers(@Argument Integer limit) {
        return service.findLatest(limit == null ? 10 : limit);
    }

    @QueryMapping
    public List<TaxpayerReadModel> taxpayersByTag(@Argument String tag) {
        LOG.info("graphql query taxpayersByTag tag={}", tag);
        return service.findByTag(tag);
    }

    @MutationMapping
    public TaxpayerSummary summarizeTaxpayer(@Argument String id) {
        LOG.info("graphql mutation summarizeTaxpayer id={}", id);
        return llmSummary.summarize(id);
    }

    @BatchMapping(typeName = "Taxpayer", field = "lines")
    public Map<TaxpayerReadModel, List<LineItem>> lines(List<TaxpayerReadModel> parents) {
        return service.loadLineItemsByParent(parents);
    }
}
