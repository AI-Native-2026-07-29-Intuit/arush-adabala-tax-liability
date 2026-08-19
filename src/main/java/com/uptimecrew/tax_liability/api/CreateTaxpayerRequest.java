package com.uptimecrew.tax_liability.api;

import java.math.BigDecimal;
import java.util.Objects;

/**
 * Request body for {@code POST /api/v1/taxpayers} (W3 D5): the minimal inputs
 * {@link com.uptimecrew.tax_liability.service.TaxLiabilityService#computeLiability} needs to
 * resolve a bracket, persist the taxpayer, and publish the resulting {@code TaxpayerUpdated}
 * event through the transactional outbox.
 */
public record CreateTaxpayerRequest(String id, String displayName, String filingStatus, BigDecimal taxableAmount) {

    public CreateTaxpayerRequest {
        Objects.requireNonNull(id, "id must not be null");
        Objects.requireNonNull(displayName, "displayName must not be null");
        Objects.requireNonNull(filingStatus, "filingStatus must not be null");
        Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
    }
}
