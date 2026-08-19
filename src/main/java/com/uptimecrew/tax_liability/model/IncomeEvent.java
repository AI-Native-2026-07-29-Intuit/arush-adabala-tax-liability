package com.uptimecrew.tax_liability.model;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.Objects;

/**
 * A single recorded income event for a taxpayer: a typed {@link IncomeSource}, an amount, and the
 * date it occurred. Immutable value type - money is normalized to scale 2, HALF_UP, in the
 * constructor.
 */
public final class IncomeEvent {

    private final String id;
    private final String taxpayerId;
    private final IncomeSource source;
    private final BigDecimal amount;
    private final LocalDate occurredOn;

    public IncomeEvent(String id, String taxpayerId, IncomeSource source, BigDecimal amount, LocalDate occurredOn) {
        Objects.requireNonNull(id, "id must not be null");
        Objects.requireNonNull(taxpayerId, "taxpayerId must not be null");
        Objects.requireNonNull(source, "source must not be null");
        Objects.requireNonNull(amount, "amount must not be null");
        Objects.requireNonNull(occurredOn, "occurredOn must not be null");
        if (id.isBlank()) {
            throw new IllegalArgumentException("id must not be blank");
        }
        if (taxpayerId.isBlank()) {
            throw new IllegalArgumentException("taxpayerId must not be blank");
        }
        if (amount.signum() < 0) {
            throw new IllegalArgumentException("amount must not be negative: " + amount);
        }
        this.id = id;
        this.taxpayerId = taxpayerId;
        this.source = source;
        this.amount = amount.setScale(2, RoundingMode.HALF_UP);
        this.occurredOn = occurredOn;
    }

    public String getId() {
        return id;
    }

    public String getTaxpayerId() {
        return taxpayerId;
    }

    public IncomeSource getSource() {
        return source;
    }

    public BigDecimal getAmount() {
        return amount;
    }

    public LocalDate getOccurredOn() {
        return occurredOn;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof IncomeEvent)) {
            return false;
        }
        IncomeEvent that = (IncomeEvent) o;
        return id.equals(that.id)
                && taxpayerId.equals(that.taxpayerId)
                && source == that.source
                && amount.equals(that.amount)
                && occurredOn.equals(that.occurredOn);
    }

    @Override
    public int hashCode() {
        return Objects.hash(id, taxpayerId, source, amount, occurredOn);
    }

    @Override
    public String toString() {
        return "IncomeEvent{"
                + "id='" + id + '\''
                + ", taxpayerId='" + taxpayerId + '\''
                + ", source=" + source
                + ", amount=" + amount
                + ", occurredOn=" + occurredOn
                + '}';
    }
}
