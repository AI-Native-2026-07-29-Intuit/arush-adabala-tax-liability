package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.model.TaxBracket;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Objects;
import java.util.Optional;

/**
 * Computes the tax owed on a taxable amount by delegating bracket lookup to an
 * injected {@link BracketResolver} strategy and applying the resolved bracket's rate.
 */
public final class TaxLiabilityService {

    private final BracketResolver bracketResolver;

    public TaxLiabilityService(BracketResolver bracketResolver) {
        this.bracketResolver = Objects.requireNonNull(bracketResolver, "bracketResolver must not be null");
    }

    /**
     * @param taxableAmount the amount to compute liability for, must not be null
     * @return {@code taxableAmount} multiplied by the injected strategy's resolved rate,
     *         scaled to 2 decimal places with {@link RoundingMode#HALF_UP}
     * @throws IllegalStateException if the injected strategy resolves no bracket for {@code taxableAmount}
     */
    public BigDecimal computeLiability(BigDecimal taxableAmount) {
        Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
        Optional<TaxBracket> resolved = bracketResolver.resolve(taxableAmount);
        if (resolved.isEmpty()) {
            throw new IllegalStateException("no tax bracket resolved for taxable amount: " + taxableAmount);
        }
        return taxableAmount.multiply(resolved.orElseThrow().rate()).setScale(2, RoundingMode.HALF_UP);
    }
}
