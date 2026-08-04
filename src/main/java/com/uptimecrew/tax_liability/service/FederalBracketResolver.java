package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.model.TaxBracket;

import java.math.BigDecimal;
import java.util.List;
import java.util.Objects;
import java.util.Optional;

/**
 * Deliberately simple decision rule: a fixed, hard-coded list of federal brackets checked
 * in ascending order. Day 3 replaces this with a real Strategy-pattern implementation.
 */
public final class FederalBracketResolver implements BracketResolver {

    private static final List<TaxBracket> FEDERAL_BRACKETS = List.of(
            new TaxBracket("fed-bracket-10", "FEDERAL", new BigDecimal("0.10"), new BigDecimal("0"), new BigDecimal("11600")),
            new TaxBracket("fed-bracket-12", "FEDERAL", new BigDecimal("0.12"), new BigDecimal("11600"), new BigDecimal("47150")),
            new TaxBracket("fed-bracket-22", "FEDERAL", new BigDecimal("0.22"), new BigDecimal("47150"), new BigDecimal("100525")),
            new TaxBracket("fed-bracket-24", "FEDERAL", new BigDecimal("0.24"), new BigDecimal("100525"), null)
    );

    @Override
    public Optional<TaxBracket> resolve(BigDecimal taxableAmount) {
        Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
        if (taxableAmount.signum() < 0) {
            throw new IllegalArgumentException("taxableAmount must not be negative: " + taxableAmount);
        }
        for (TaxBracket bracket : FEDERAL_BRACKETS) {
            if (bracket.covers(taxableAmount)) {
                return Optional.of(bracket);
            }
        }
        return Optional.empty();
    }

    @Override
    public boolean equals(Object o) {
        return o instanceof FederalBracketResolver;
    }

    @Override
    public int hashCode() {
        return FederalBracketResolver.class.hashCode();
    }

    @Override
    public String toString() {
        return "FederalBracketResolver{}";
    }
}
