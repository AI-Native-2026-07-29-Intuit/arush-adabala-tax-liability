package com.uptimecrew.tax_liability.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Objects;

/**
 * Maps to {@code taxcalc.liability} (W2 D1): one computed liability per taxpayer per
 * tax year, the child side of the {@link Taxpayer} parent/child relationship.
 *
 * <p>Unlike {@link Taxpayer} and {@link Bracket}, this entity has no single {@code
 * private String id} field: {@code taxcalc.liability} was deliberately given a composite
 * primary key {@code (taxpayer_id, tax_year)} rather than a surrogate id (see the
 * "Trade-offs" section of {@code db/README.md} from W2 D1 for the reasoning — a surrogate
 * id would need an extra {@code UNIQUE (taxpayer_id, tax_year)} constraint to express the
 * same invariant, plus a column nothing else references). This entity mirrors that DDL
 * exactly via {@link LiabilityId} rather than introducing a surrogate id the schema
 * doesn't have.
 */
@Entity
@Table(schema = "taxcalc", name = "liability")
@IdClass(LiabilityId.class)
public class Liability {

    @Id
    @Column(name = "taxpayer_id", length = 64)
    private String taxpayerId;

    @Id
    @Column(name = "tax_year")
    private Integer taxYear;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "taxpayer_id", insertable = false, updatable = false)
    private Taxpayer taxpayer;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "bracket_id", nullable = false)
    private Bracket bracket;

    @Column(name = "taxable_amount", nullable = false)
    private BigDecimal taxableAmount;

    @Column(name = "liability_amount", nullable = false)
    private BigDecimal liabilityAmount;

    @Column(name = "computed_at", nullable = false)
    private Instant computedAt;

    protected Liability() {
    }

    public Liability(Taxpayer taxpayer, Integer taxYear, Bracket bracket, BigDecimal taxableAmount,
            BigDecimal liabilityAmount, Instant computedAt) {
        this.taxpayer = Objects.requireNonNull(taxpayer, "taxpayer must not be null");
        this.taxpayerId = taxpayer.getId();
        this.taxYear = Objects.requireNonNull(taxYear, "taxYear must not be null");
        this.bracket = Objects.requireNonNull(bracket, "bracket must not be null");
        this.taxableAmount = Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
        this.liabilityAmount = Objects.requireNonNull(liabilityAmount, "liabilityAmount must not be null");
        this.computedAt = Objects.requireNonNull(computedAt, "computedAt must not be null");
        if (taxableAmount.signum() < 0) {
            throw new IllegalArgumentException("taxableAmount must not be negative: " + taxableAmount);
        }
        if (liabilityAmount.signum() < 0) {
            throw new IllegalArgumentException("liabilityAmount must not be negative: " + liabilityAmount);
        }
    }

    public String getTaxpayerId() {
        return taxpayerId;
    }

    public Integer getTaxYear() {
        return taxYear;
    }

    public Taxpayer getTaxpayer() {
        return taxpayer;
    }

    public Bracket getBracket() {
        return bracket;
    }

    public BigDecimal getTaxableAmount() {
        return taxableAmount;
    }

    public BigDecimal getLiabilityAmount() {
        return liabilityAmount;
    }

    public Instant getComputedAt() {
        return computedAt;
    }

    @Override
    public boolean equals(Object o) {
        return o instanceof Liability other
                && Objects.equals(taxpayerId, other.taxpayerId)
                && Objects.equals(taxYear, other.taxYear);
    }

    @Override
    public int hashCode() {
        return Objects.hash(taxpayerId, taxYear);
    }

    @Override
    public String toString() {
        return "Liability{taxpayerId=" + taxpayerId + ", taxYear=" + taxYear + ", taxableAmount=" + taxableAmount
                + ", liabilityAmount=" + liabilityAmount + ", computedAt=" + computedAt + "}";
    }
}
