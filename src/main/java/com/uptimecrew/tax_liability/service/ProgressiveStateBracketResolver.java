package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.exception.BracketResolutionFailedException;
import com.uptimecrew.tax_liability.exception.InvalidIncomeException;
import com.uptimecrew.tax_liability.model.TaxBracket;

import java.io.IOException;
import java.math.BigDecimal;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.Optional;

/**
 * Strategy for a state that taxes income progressively across multiple marginal-rate
 * brackets, unlike {@link FlatStateBracketResolver}'s single flat rate.
 */
public final class ProgressiveStateBracketResolver implements BracketResolver {

    /**
     * Amounts at or above this threshold require the extended bracket table page, whose
     * synthetic load failure below demonstrates the {@link BracketResolutionFailedException}
     * failure path.
     */
    private static final BigDecimal EXTENDED_TABLE_THRESHOLD = new BigDecimal("1000000000");

    private final String jurisdiction;
    private final List<TaxBracket> brackets;

    public ProgressiveStateBracketResolver(String jurisdiction, List<TaxBracket> brackets) {
        Objects.requireNonNull(jurisdiction, "jurisdiction must not be null");
        Objects.requireNonNull(brackets, "brackets must not be null");
        if (jurisdiction.isBlank()) {
            throw new IllegalArgumentException("jurisdiction must not be blank");
        }
        if (brackets.isEmpty()) {
            throw new IllegalArgumentException("brackets must not be empty");
        }
        for (TaxBracket bracket : brackets) {
            if (!bracket.jurisdiction().equals(jurisdiction)) {
                throw new IllegalArgumentException(
                        "bracket " + bracket.id() + " does not belong to jurisdiction " + jurisdiction);
            }
        }
        this.jurisdiction = jurisdiction;
        this.brackets = brackets.stream().sorted(Comparator.comparing(TaxBracket::floor)).toList();
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
        return findCoveringBracket(taxableAmount);
    }

    private Optional<TaxBracket> findCoveringBracket(BigDecimal taxableAmount) {
        for (TaxBracket bracket : brackets) {
            if (bracket.covers(taxableAmount)) {
                return Optional.of(bracket);
            }
        }
        return Optional.empty();
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof ProgressiveStateBracketResolver)) {
            return false;
        }
        ProgressiveStateBracketResolver that = (ProgressiveStateBracketResolver) o;
        return jurisdiction.equals(that.jurisdiction) && brackets.equals(that.brackets);
    }

    @Override
    public int hashCode() {
        return Objects.hash(jurisdiction, brackets);
    }

    @Override
    public String toString() {
        return "ProgressiveStateBracketResolver{"
                + "jurisdiction='" + jurisdiction + '\''
                + ", brackets=" + brackets.size()
                + '}';
    }
}
