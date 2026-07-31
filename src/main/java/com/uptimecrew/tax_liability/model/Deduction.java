package com.uptimecrew.tax_liability.model;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.Objects;

public final class Deduction {

    private final String id;
    private final String taxpayerId;
    private final String kind;
    private final BigDecimal amount;
    private final LocalDate claimedFor;

    public Deduction(String id, String taxpayerId, String kind, BigDecimal amount, LocalDate claimedFor) {
        Objects.requireNonNull(id, "id must not be null");
        Objects.requireNonNull(taxpayerId, "taxpayerId must not be null");
        Objects.requireNonNull(kind, "kind must not be null");
        Objects.requireNonNull(amount, "amount must not be null");
        Objects.requireNonNull(claimedFor, "claimedFor must not be null");
        if (id.isBlank()) {
            throw new IllegalArgumentException("id must not be blank");
        }
        if (taxpayerId.isBlank()) {
            throw new IllegalArgumentException("taxpayerId must not be blank");
        }
        if (kind.isBlank()) {
            throw new IllegalArgumentException("kind must not be blank");
        }
        if (amount.signum() < 0) {
            throw new IllegalArgumentException("amount must not be negative: " + amount);
        }
        this.id = id;
        this.taxpayerId = taxpayerId;
        this.kind = kind;
        this.amount = amount.setScale(2, RoundingMode.HALF_UP);
        this.claimedFor = claimedFor;
    }

    public String getId() {
        return id;
    }

    public String getTaxpayerId() {
        return taxpayerId;
    }

    public String getKind() {
        return kind;
    }

    public BigDecimal getAmount() {
        return amount;
    }

    public LocalDate getClaimedFor() {
        return claimedFor;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof Deduction)) {
            return false;
        }
        Deduction that = (Deduction) o;
        return id.equals(that.id)
                && taxpayerId.equals(that.taxpayerId)
                && kind.equals(that.kind)
                && amount.equals(that.amount)
                && claimedFor.equals(that.claimedFor);
    }

    @Override
    public int hashCode() {
        return Objects.hash(id, taxpayerId, kind, amount, claimedFor);
    }

    @Override
    public String toString() {
        return "Deduction{"
                + "id='" + id + '\''
                + ", taxpayerId='" + taxpayerId + '\''
                + ", kind='" + kind + '\''
                + ", amount=" + amount
                + ", claimedFor=" + claimedFor
                + '}';
    }
}
