package com.uptimecrew.tax_liability.model;

import java.math.BigDecimal;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Covers {@link TaxBracket}'s constructor validation, {@code covers(BigDecimal)} range check
 * (including an unbounded top bracket with a {@code null} ceiling), and {@code equals}/{@code
 * hashCode}.
 */
class TaxBracketTest {

    @Test
    void constructs_with_valid_inputs() {
        TaxBracket subject = new TaxBracket(
                "fed-bracket-10",
                "FEDERAL",
                new BigDecimal("0.10"),
                new BigDecimal("0"),
                new BigDecimal("11600")
        );
        assertEquals("fed-bracket-10", subject.id());
        assertEquals("FEDERAL", subject.jurisdiction());
        assertEquals(0, new BigDecimal("0.10").compareTo(subject.rate()));
        assertEquals(0, new BigDecimal("0").compareTo(subject.floor()));
        assertEquals(0, new BigDecimal("11600").compareTo(subject.ceiling()));
    }

    @Test
    void rejects_null_rate() {
        assertThrows(NullPointerException.class, () -> new TaxBracket(
                "fed-bracket-10", "FEDERAL", null, new BigDecimal("0"), new BigDecimal("11600")
        ));
    }

    @Test
    void rejects_ceiling_at_or_below_floor() {
        assertThrows(IllegalArgumentException.class, () -> new TaxBracket(
                "fed-bracket-10", "FEDERAL", new BigDecimal("0.10"), new BigDecimal("11600"), new BigDecimal("11600")
        ));
    }

    @Test
    void allows_null_ceiling_for_top_bracket() {
        TaxBracket subject = new TaxBracket(
                "fed-bracket-24", "FEDERAL", new BigDecimal("0.24"), new BigDecimal("100525"), null
        );
        assertTrue(subject.covers(new BigDecimal("500000")));
        assertFalse(subject.covers(new BigDecimal("100000")));
    }

    @Test
    void equal_instances_have_equal_hashcodes() {
        TaxBracket a = new TaxBracket("fed-bracket-10", "FEDERAL", new BigDecimal("0.10"), new BigDecimal("0"), new BigDecimal("11600"));
        TaxBracket b = new TaxBracket("fed-bracket-10", "FEDERAL", new BigDecimal("0.10"), new BigDecimal("0"), new BigDecimal("11600"));
        assertEquals(a, b);
        assertEquals(a.hashCode(), b.hashCode());
    }
}
