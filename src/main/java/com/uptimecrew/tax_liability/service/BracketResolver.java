package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.model.TaxBracket;

import java.math.BigDecimal;
import java.util.Optional;

public interface BracketResolver {

    /**
     * Returns the single {@link TaxBracket} that {@code taxableAmount} falls into,
     * or {@link Optional#empty()} if no bracket covers it.
     */
    Optional<TaxBracket> resolve(BigDecimal taxableAmount);
}
