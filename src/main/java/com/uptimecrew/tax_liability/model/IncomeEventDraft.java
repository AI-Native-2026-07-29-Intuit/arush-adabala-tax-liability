package com.uptimecrew.tax_liability.model;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.Objects;

/**
 * An unresolved income record: an amount, a free-text {@code source}, and the date it occurred.
 * Unlike {@link IncomeEvent}, {@code source} here is a plain {@link String} rather than a typed
 * {@link IncomeSource}, and there is no {@code taxpayerId} - both left for a later stage to
 * resolve. Immutable value type - money is normalized to scale 2, HALF_UP, in the constructor.
 */
public final class IncomeEventDraft {

    private final String id;
    private final BigDecimal amount;
    private final String source;
    private final LocalDate occurredOn;

    public IncomeEventDraft(String id, BigDecimal amount, String source, LocalDate occurredOn) {
        Objects.requireNonNull(id, "id must not be null");
        Objects.requireNonNull(amount, "amount must not be null");
        Objects.requireNonNull(source, "source must not be null");
        Objects.requireNonNull(occurredOn, "occurredOn must not be null");
        if (id.isBlank()) {
            throw new IllegalArgumentException("id must not be blank");
        }
        if (amount.signum() < 0) {
            throw new IllegalArgumentException("amount must not be negative: " + amount);
        }
        this.id = id;
        this.amount = amount.setScale(2, RoundingMode.HALF_UP);
        this.source = source;
        this.occurredOn = occurredOn;
    }

    public String getId() {
        return id;
    }

    public BigDecimal getAmount() {
        return amount;
    }

    public String getSource() {
        return source;
    }

    public LocalDate getOccurredOn() {
        return occurredOn;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof IncomeEventDraft)) {
            return false;
        }
        IncomeEventDraft that = (IncomeEventDraft) o;
        return id.equals(that.id)
                && amount.equals(that.amount)
                && source.equals(that.source)
                && occurredOn.equals(that.occurredOn);
    }

    @Override
    public int hashCode() {
        return Objects.hash(id, amount, source, occurredOn);
    }

    @Override
    public String toString() {
        return "IncomeEventDraft{"
                + "id='" + id + '\''
                + ", amount=" + amount
                + ", source='" + source + '\''
                + ", occurredOn=" + occurredOn
                + '}';
    }
}
