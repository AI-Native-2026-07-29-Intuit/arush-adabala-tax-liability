package com.uptimecrew.tax_liability.model;

import java.math.BigDecimal;
import java.time.LocalDate;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class DeductionTest {

    @Test
    void constructs_with_valid_inputs() {
        Deduction subject = new Deduction(
                "ded-synth-001",
                "taxpayer-001",
                "STANDARD",
                new BigDecimal("14600.00"),
                LocalDate.of(2026, 12, 31)
        );
        assertEquals("ded-synth-001", subject.getId());
        assertEquals("taxpayer-001", subject.getTaxpayerId());
        assertEquals("STANDARD", subject.getKind());
        assertEquals(0, new BigDecimal("14600.00").compareTo(subject.getAmount()));
        assertEquals(LocalDate.of(2026, 12, 31), subject.getClaimedFor());
    }

    @Test
    void rejects_null_kind() {
        assertThrows(NullPointerException.class, () -> new Deduction(
                "ded-synth-001", "taxpayer-001", null, new BigDecimal("14600.00"), LocalDate.of(2026, 12, 31)
        ));
    }

    @Test
    void rejects_negative_amount() {
        assertThrows(IllegalArgumentException.class, () -> new Deduction(
                "ded-synth-001", "taxpayer-001", "STANDARD", new BigDecimal("-1.00"), LocalDate.of(2026, 12, 31)
        ));
    }

    @Test
    void equal_instances_have_equal_hashcodes() {
        Deduction a = new Deduction("ded-synth-001", "taxpayer-001", "STANDARD", new BigDecimal("14600.00"), LocalDate.of(2026, 12, 31));
        Deduction b = new Deduction("ded-synth-001", "taxpayer-001", "STANDARD", new BigDecimal("14600.00"), LocalDate.of(2026, 12, 31));
        assertEquals(a, b);
        assertEquals(a.hashCode(), b.hashCode());
    }
}
