package com.uptimecrew.tax_liability.model;

import java.math.BigDecimal;
import java.util.Objects;

public final class TaxBracket {

    private final String id;
    private final String jurisdiction;
    private final BigDecimal rate;
    private final BigDecimal floor;
    private final BigDecimal ceiling;

    /**
     * {@code ceiling} may be {@code null} to represent an unbounded top bracket.
     */
    public TaxBracket(String id, String jurisdiction, BigDecimal rate, BigDecimal floor, BigDecimal ceiling) {
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
        this.id = id;
        this.jurisdiction = jurisdiction;
        this.rate = rate;
        this.floor = floor;
        this.ceiling = ceiling;
    }

    public String getId() {
        return id;
    }

    public String getJurisdiction() {
        return jurisdiction;
    }

    public BigDecimal getRate() {
        return rate;
    }

    public BigDecimal getFloor() {
        return floor;
    }

    public BigDecimal getCeiling() {
        return ceiling;
    }

    public boolean covers(BigDecimal taxableAmount) {
        Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
        boolean atOrAboveFloor = taxableAmount.compareTo(floor) >= 0;
        boolean belowCeiling = ceiling == null || taxableAmount.compareTo(ceiling) < 0;
        return atOrAboveFloor && belowCeiling;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof TaxBracket)) {
            return false;
        }
        TaxBracket that = (TaxBracket) o;
        return id.equals(that.id)
                && jurisdiction.equals(that.jurisdiction)
                && rate.equals(that.rate)
                && floor.equals(that.floor)
                && Objects.equals(ceiling, that.ceiling);
    }

    @Override
    public int hashCode() {
        return Objects.hash(id, jurisdiction, rate, floor, ceiling);
    }

    @Override
    public String toString() {
        return "TaxBracket{"
                + "id='" + id + '\''
                + ", jurisdiction='" + jurisdiction + '\''
                + ", rate=" + rate
                + ", floor=" + floor
                + ", ceiling=" + ceiling
                + '}';
    }
}
