package com.uptimecrew.tax_liability.model;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Objects;

/**
 * {@code ceiling} may be {@code null} to represent an unbounded top bracket.
 */
public record TaxBracket(String id, String jurisdiction, BigDecimal rate, BigDecimal floor, BigDecimal ceiling) {

    public TaxBracket {
        Objects.requireNonNull(id, "id must not be null");
        Objects.requireNonNull(jurisdiction, "jurisdiction must not be null");
        Objects.requireNonNull(rate, "rate must not be null");
        Objects.requireNonNull(floor, "floor must not be null");
        if (id.isBlank()) {
            throw new IllegalArgumentException("id must not be blank");
        }
        if (jurisdiction.isBlank()) {
            throw new IllegalArgumentException("jurisdiction must not be blank");
        }
        if (rate.signum() < 0) {
            throw new IllegalArgumentException("rate must not be negative: " + rate);
        }
        if (floor.signum() < 0) {
            throw new IllegalArgumentException("floor must not be negative: " + floor);
        }
        if (ceiling != null && ceiling.compareTo(floor) <= 0) {
            throw new IllegalArgumentException("ceiling must be greater than floor: " + ceiling + " <= " + floor);
        }
        floor = floor.setScale(2, RoundingMode.HALF_UP);
        ceiling = ceiling == null ? null : ceiling.setScale(2, RoundingMode.HALF_UP);
    }

    public boolean covers(BigDecimal taxableAmount) {
        Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
        boolean atOrAboveFloor = taxableAmount.compareTo(floor) >= 0;
        boolean belowCeiling = ceiling == null || taxableAmount.compareTo(ceiling) < 0;
        return atOrAboveFloor && belowCeiling;
    }
}
