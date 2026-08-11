package com.uptimecrew.tax_liability.entity;

import java.io.Serializable;
import java.util.Objects;

/**
 * Composite primary key for {@link Liability}, mirroring {@code taxcalc.liability}'s
 * {@code PRIMARY KEY (taxpayer_id, tax_year)} (W2 D1) — one computed liability per
 * taxpayer per tax year.
 */
public final class LiabilityId implements Serializable {

    private String taxpayerId;
    private Integer taxYear;

    protected LiabilityId() {
    }

    public LiabilityId(String taxpayerId, Integer taxYear) {
        this.taxpayerId = Objects.requireNonNull(taxpayerId, "taxpayerId must not be null");
        this.taxYear = Objects.requireNonNull(taxYear, "taxYear must not be null");
        if (taxpayerId.isBlank()) {
            throw new IllegalArgumentException("taxpayerId must not be blank");
        }
    }

    public String getTaxpayerId() {
        return taxpayerId;
    }

    public Integer getTaxYear() {
        return taxYear;
    }

    @Override
    public boolean equals(Object o) {
        return o instanceof LiabilityId other
                && Objects.equals(taxpayerId, other.taxpayerId)
                && Objects.equals(taxYear, other.taxYear);
    }

    @Override
    public int hashCode() {
        return Objects.hash(taxpayerId, taxYear);
    }

    @Override
    public String toString() {
        return "LiabilityId{taxpayerId=" + taxpayerId + ", taxYear=" + taxYear + "}";
    }
}
