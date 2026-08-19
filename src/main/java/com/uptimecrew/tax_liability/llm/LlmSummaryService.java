package com.uptimecrew.tax_liability.llm;

import java.math.BigDecimal;
import java.util.Objects;

import com.uptimecrew.tax_liability.graphql.TaxpayerSummary;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.service.TaxLiabilityService;

import org.springframework.stereotype.Service;

/**
 * Backs the {@code summarizeTaxpayer} GraphQL mutation (W3 D4). For now this computes the
 * {@link TaxpayerSummary} deterministically from the read model - mirroring the stub LLM call in
 * {@link com.uptimecrew.tax_liability.api.TaxpayerController#summary}. Task 3 of this deliverable
 * replaces the body with a Spring AI {@code ChatClient} call bound to this same record via
 * {@code .entity(TaxpayerSummary.class)} and re-validated against a hand-written JSON Schema.
 */
@Service
public class LlmSummaryService {

    private static final BigDecimal HIGH_RISK_THRESHOLD = BigDecimal.valueOf(50_000);
    private static final BigDecimal MEDIUM_RISK_THRESHOLD = BigDecimal.valueOf(10_000);

    private final TaxLiabilityService service;

    public LlmSummaryService(TaxLiabilityService service) {
        this.service = Objects.requireNonNull(service, "service must not be null");
    }

    public TaxpayerSummary summarize(String id) {
        Objects.requireNonNull(id, "id must not be null");
        TaxpayerReadModel taxpayer = service.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("unknown id " + id));

        BigDecimal totalLiability = taxpayer.getLiabilities().stream()
                .map(TaxpayerReadModel.EmbeddedLiability::getLiabilityAmount)
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        long jurisdictionCount = taxpayer.getLiabilities().stream()
                .map(TaxpayerReadModel.EmbeddedLiability::getBracketId)
                .distinct()
                .count();

        return new TaxpayerSummary(taxpayer.getFilingStatus(), totalLiability.doubleValue(),
                (int) jurisdictionCount, riskBandFor(totalLiability));
    }

    private String riskBandFor(BigDecimal totalLiability) {
        if (totalLiability.compareTo(HIGH_RISK_THRESHOLD) >= 0) {
            return "HIGH";
        }
        if (totalLiability.compareTo(MEDIUM_RISK_THRESHOLD) >= 0) {
            return "MEDIUM";
        }
        return "LOW";
    }
}
