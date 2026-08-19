package com.uptimecrew.tax_liability.graphql;

import java.util.Objects;

/**
 * Return type of the {@code summarizeTaxpayer} GraphQL mutation (W3 D4). A record so graphql-java
 * resolves each schema field straight off the accessor methods, and so Spring AI's structured-
 * output converter (Task 3 of this deliverable) can bind an LLM response directly to it.
 */
public record TaxpayerSummary(String filingStatus, Double totalLiability, Integer jurisdictionCount,
        String riskBand) {

    public TaxpayerSummary {
        Objects.requireNonNull(filingStatus, "filingStatus must not be null");
        Objects.requireNonNull(totalLiability, "totalLiability must not be null");
        Objects.requireNonNull(jurisdictionCount, "jurisdictionCount must not be null");
        Objects.requireNonNull(riskBand, "riskBand must not be null");
    }
}
