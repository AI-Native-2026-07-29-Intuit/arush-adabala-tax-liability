package com.uptimecrew.tax_liability.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.math.BigDecimal;
import java.util.Objects;

/**
 * Maps to {@code taxcalc.bracket} (W2 D1): tax brackets indexed by (jurisdiction, year, code).
 */
@Entity
@Table(schema = "taxcalc", name = "bracket")
public class Bracket {

    @Id
    @Column(length = 64)
    private String id;

    @Column(name = "jurisdiction", nullable = false)
    private String jurisdiction;

    @Column(name = "tax_year", nullable = false)
    private Integer taxYear;

    @Column(name = "code", nullable = false)
    private String code;

    @Column(name = "rate", nullable = false)
    private BigDecimal rate;

    @Column(name = "floor_amount", nullable = false)
    private BigDecimal floorAmount;

    @Column(name = "ceiling_amount")
    private BigDecimal ceilingAmount;

    protected Bracket() {
    }

    public Bracket(String id, String jurisdiction, Integer taxYear, String code, BigDecimal rate,
            BigDecimal floorAmount, BigDecimal ceilingAmount) {
        this.id = Objects.requireNonNull(id, "id must not be null");
        this.jurisdiction = Objects.requireNonNull(jurisdiction, "jurisdiction must not be null");
        this.taxYear = Objects.requireNonNull(taxYear, "taxYear must not be null");
        this.code = Objects.requireNonNull(code, "code must not be null");
        this.rate = Objects.requireNonNull(rate, "rate must not be null");
        this.floorAmount = Objects.requireNonNull(floorAmount, "floorAmount must not be null");
        this.ceilingAmount = ceilingAmount;
        if (id.isBlank()) {
            throw new IllegalArgumentException("id must not be blank");
        }
        if (jurisdiction.isBlank()) {
            throw new IllegalArgumentException("jurisdiction must not be blank");
        }
        if (code.isBlank()) {
            throw new IllegalArgumentException("code must not be blank");
        }
        if (rate.signum() < 0) {
            throw new IllegalArgumentException("rate must not be negative: " + rate);
        }
        if (floorAmount.signum() < 0) {
            throw new IllegalArgumentException("floorAmount must not be negative: " + floorAmount);
        }
        if (ceilingAmount != null && ceilingAmount.compareTo(floorAmount) <= 0) {
            throw new IllegalArgumentException("ceilingAmount must be greater than floorAmount: "
                    + ceilingAmount + " <= " + floorAmount);
        }
    }

    public String getId() {
        return id;
    }

    public String getJurisdiction() {
        return jurisdiction;
    }

    public Integer getTaxYear() {
        return taxYear;
    }

    public String getCode() {
        return code;
    }

    public BigDecimal getRate() {
        return rate;
    }

    public BigDecimal getFloorAmount() {
        return floorAmount;
    }

    public BigDecimal getCeilingAmount() {
        return ceilingAmount;
    }

    @Override
    public boolean equals(Object o) {
        return o instanceof Bracket other && Objects.equals(id, other.id);
    }

    @Override
    public int hashCode() {
        return Objects.hashCode(id);
    }

    @Override
    public String toString() {
        return "Bracket{id=" + id + ", jurisdiction=" + jurisdiction + ", taxYear=" + taxYear
                + ", code=" + code + ", rate=" + rate + ", floorAmount=" + floorAmount
                + ", ceilingAmount=" + ceilingAmount + "}";
    }
}
