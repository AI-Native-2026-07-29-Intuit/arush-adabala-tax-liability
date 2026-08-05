package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.exception.BracketResolutionFailedException;
import com.uptimecrew.tax_liability.exception.InvalidIncomeException;
import com.uptimecrew.tax_liability.model.TaxBracket;

import java.io.IOException;
import java.math.BigDecimal;
import java.util.Objects;
import java.util.Optional;

/**
 * Strategy for a state that taxes all income at a single flat rate, with no brackets.
 */
public final class FlatStateBracketResolver implements BracketResolver {

    /**
     * Amounts at or above this threshold require the extended bracket table page, whose
     * synthetic load failure below demonstrates the {@link BracketResolutionFailedException}
     * failure path.
     */
    private static final BigDecimal EXTENDED_TABLE_THRESHOLD = new BigDecimal("1000000000");

    private final String jurisdiction;
    private final BigDecimal rate;
    private final TaxBracket bracket;

    public FlatStateBracketResolver(String jurisdiction, BigDecimal rate) {
        Objects.requireNonNull(jurisdiction, "jurisdiction must not be null");
        Objects.requireNonNull(rate, "rate must not be null");
        if (jurisdiction.isBlank()) {
            throw new IllegalArgumentException("jurisdiction must not be blank");
        }
        if (rate.signum() < 0) {
            throw new IllegalArgumentException("rate must not be negative: " + rate);
        }
        this.jurisdiction = jurisdiction;
        this.rate = rate;
        this.bracket = new TaxBracket(
                "state-flat-" + jurisdiction.toLowerCase(), jurisdiction, rate, BigDecimal.ZERO, null);
    }

    @Override
    public Optional<TaxBracket> resolve(BigDecimal taxableAmount) {
        Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
        if (taxableAmount.signum() < 0) {
            throw new InvalidIncomeException("taxableAmount must be non-null and non-negative: " + taxableAmount);
        }
        if (taxableAmount.compareTo(EXTENDED_TABLE_THRESHOLD) >= 0) {
            try {
                /* simulate a read that could fail in production */
                throw new IOException("extended bracket table unreachable for jurisdiction " + jurisdiction);
            } catch (IOException cause) {
                throw new BracketResolutionFailedException(
                        "failed loading extended bracket table for jurisdiction " + jurisdiction, cause);
            }
        }
        return Optional.of(bracket);
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof FlatStateBracketResolver)) {
            return false;
        }
        FlatStateBracketResolver that = (FlatStateBracketResolver) o;
        return jurisdiction.equals(that.jurisdiction) && rate.equals(that.rate);
    }

    @Override
    public int hashCode() {
        return Objects.hash(jurisdiction, rate);
    }

    @Override
    public String toString() {
        return "FlatStateBracketResolver{"
                + "jurisdiction='" + jurisdiction + '\''
                + ", rate=" + rate
                + '}';
    }
}
