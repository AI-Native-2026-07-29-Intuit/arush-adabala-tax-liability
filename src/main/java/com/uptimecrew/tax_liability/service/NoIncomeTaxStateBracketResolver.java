package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.model.TaxBracket;

import java.math.BigDecimal;
import java.util.Objects;
import java.util.Optional;

/**
 * Strategy for a state that levies no income tax at all.
 */
public final class NoIncomeTaxStateBracketResolver implements BracketResolver {

    private final String jurisdiction;
    private final TaxBracket bracket;

    public NoIncomeTaxStateBracketResolver(String jurisdiction) {
        Objects.requireNonNull(jurisdiction, "jurisdiction must not be null");
        if (jurisdiction.isBlank()) {
            throw new IllegalArgumentException("jurisdiction must not be blank");
        }
        this.jurisdiction = jurisdiction;
        this.bracket = new TaxBracket(
                "state-no-tax-" + jurisdiction.toLowerCase(), jurisdiction, BigDecimal.ZERO, BigDecimal.ZERO, null);
    }

    @Override
    public Optional<TaxBracket> resolve(BigDecimal taxableAmount) {
        Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
        if (taxableAmount.signum() < 0) {
            throw new IllegalArgumentException("taxableAmount must not be negative: " + taxableAmount);
        }
        return Optional.of(bracket);
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof NoIncomeTaxStateBracketResolver)) {
            return false;
        }
        NoIncomeTaxStateBracketResolver that = (NoIncomeTaxStateBracketResolver) o;
        return jurisdiction.equals(that.jurisdiction);
    }

    @Override
    public int hashCode() {
        return Objects.hash(jurisdiction);
    }

    @Override
    public String toString() {
        return "NoIncomeTaxStateBracketResolver{jurisdiction='" + jurisdiction + '\'' + '}';
    }
}
