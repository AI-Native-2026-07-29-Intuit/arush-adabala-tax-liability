package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.model.TaxBracket;

import java.math.BigDecimal;
import java.util.Optional;

/**
 * A jurisdiction-specific strategy for resolving which {@link TaxBracket} a taxable amount falls
 * into. {@link TaxLiabilityService} takes one of these via constructor injection rather than
 * choosing a jurisdiction's rules itself; {@link FederalBracketResolver}, {@link
 * FlatStateBracketResolver}, {@link NoIncomeTaxStateBracketResolver}, and {@link
 * ProgressiveStateBracketResolver} are the interchangeable implementations.
 */
public interface BracketResolver {

    /**
     * Returns the single {@link TaxBracket} that {@code taxableAmount} falls into,
     * or {@link Optional#empty()} if no bracket covers it.
     */
    Optional<TaxBracket> resolve(BigDecimal taxableAmount);
}
