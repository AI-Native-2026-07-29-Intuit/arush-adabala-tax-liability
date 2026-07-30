package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.model.TaxBracket;

import java.math.BigDecimal;

public interface BracketResolver {

    /**
     * Returns the single {@link TaxBracket} that {@code taxableAmount} falls into.
     */
    TaxBracket resolve(BigDecimal taxableAmount);
}
