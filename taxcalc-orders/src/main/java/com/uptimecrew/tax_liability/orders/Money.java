package com.uptimecrew.tax_liability.orders;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Objects;

/**
 * The one place a money value is normalised or rendered in this service.
 *
 * <p><strong>Why a helper rather than a convention.</strong> Scale-2 HALF_UP applied "wherever
 * money is handled" is applied inconsistently by the third engineer to touch it; applied here it
 * is applied once. Every amount that enters the service passes through {@link #normalise} before
 * it reaches a column, and every amount that leaves passes through {@link #render}.
 *
 * <p><strong>Why {@code toPlainString} and not {@code toString}.</strong> {@code BigDecimal}'s
 * own {@code toString} switches to scientific notation for some values - {@code 1E+2} rather
 * than {@code 100.00} - and the consuming Pydantic model would reject that, having been told the
 * field is a decimal string. It is an edge case that never fires in a test with tidy fixtures and
 * fires in production on a value nobody tried.
 */
public final class Money {

    /** Every monetary column in this service is {@code NUMERIC(12, 2)}; this is the 2. */
    public static final int SCALE = 2;

    private Money() {
        throw new AssertionError("Money is a utility holder and is not instantiable");
    }

    /**
     * Returns {@code amount} at the canonical money scale.
     *
     * @param amount the value to normalise
     * @return the value at scale {@link #SCALE}, rounded HALF_UP
     * @throws NullPointerException if {@code amount} is {@code null}
     * @throws IllegalArgumentException if {@code amount} is zero or negative - a refund of
     *     nothing is not a refund, and a negative refund is a charge wearing the wrong name
     */
    public static BigDecimal normalise(BigDecimal amount) {
        Objects.requireNonNull(amount, "amount must not be null");
        if (amount.signum() <= 0) {
            throw new IllegalArgumentException("amount must be greater than zero, got " + amount);
        }
        return amount.setScale(SCALE, RoundingMode.HALF_UP);
    }

    /**
     * Renders {@code amount} as the exact decimal string that crosses the wire.
     *
     * @param amount the value to render
     * @return a plain decimal string at scale {@link #SCALE}
     * @throws NullPointerException if {@code amount} is {@code null}
     */
    public static String render(BigDecimal amount) {
        Objects.requireNonNull(amount, "amount must not be null");
        return amount.setScale(SCALE, RoundingMode.HALF_UP).toPlainString();
    }
}
