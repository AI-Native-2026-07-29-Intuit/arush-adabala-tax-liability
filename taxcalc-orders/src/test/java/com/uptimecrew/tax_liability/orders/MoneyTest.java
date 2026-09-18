package com.uptimecrew.tax_liability.orders;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.math.BigDecimal;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * Pins the money rules this service is judged by.
 *
 * <p>Each test here corresponds to a way a money value silently stops being correct: the scale
 * disappearing, a rounding direction being chosen by the default rather than by a decision, a
 * value rendering in scientific notation, or a refund of nothing being accepted as a refund.
 */
class MoneyTest {

    @Test
    @DisplayName("normalise pads to scale 2, because the scale is part of the value")
    void normalisePadsToScaleTwo() {
        assertEquals(new BigDecimal("10.00"), Money.normalise(new BigDecimal("10")));
        assertEquals("10.00", Money.normalise(new BigDecimal("10")).toPlainString());
    }

    @Test
    @DisplayName("normalise rounds HALF_UP, not HALF_EVEN")
    void normaliseRoundsHalfUp() {
        // The JDK's default for setScale is to throw; the default people reach for when it does
        // is HALF_EVEN, which rounds 0.125 to 0.12. Money rounds away from zero at the half.
        assertEquals(new BigDecimal("0.13"), Money.normalise(new BigDecimal("0.125")));
        assertEquals(new BigDecimal("0.14"), Money.normalise(new BigDecimal("0.135")));
    }

    @Test
    @DisplayName("render never emits scientific notation")
    void renderIsAlwaysPlain() {
        // BigDecimal.toString() renders this as 1E+2. A consumer told the field is a decimal
        // string rejects that - an edge case that never fires on tidy fixtures and fires in
        // production on a value nobody tried.
        assertEquals("100.00", Money.render(new BigDecimal("1E+2")));
    }

    @Test
    @DisplayName("a zero or negative amount is not a refund")
    void rejectsNonPositiveAmounts() {
        assertThrows(IllegalArgumentException.class, () -> Money.normalise(BigDecimal.ZERO));
        assertThrows(
                IllegalArgumentException.class, () -> Money.normalise(new BigDecimal("-0.01")));
    }

    @Test
    @DisplayName("a null amount is rejected at the boundary, not dereferenced later")
    void rejectsNull() {
        assertThrows(NullPointerException.class, () -> Money.normalise(null));
        assertThrows(NullPointerException.class, () -> Money.render(null));
    }
}
