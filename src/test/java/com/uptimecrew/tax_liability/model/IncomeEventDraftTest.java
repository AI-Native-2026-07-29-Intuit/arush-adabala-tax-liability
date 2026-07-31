package com.uptimecrew.tax_liability.model;

import java.math.BigDecimal;
import java.time.LocalDate;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class IncomeEventDraftTest {

    @Test
    void constructs_with_valid_inputs() {
        IncomeEventDraft subject = new IncomeEventDraft(
            "inc-synth-001",
            new BigDecimal("75000.00"),
            "WAGES",
            LocalDate.of(2026, 3, 1)
        );
        assertEquals("inc-synth-001", subject.getId());
        assertEquals(0, new BigDecimal("75000.00").compareTo(subject.getAmount()));
        assertEquals("WAGES", subject.getSource());
        assertEquals(LocalDate.of(2026, 3, 1), subject.getOccurredOn());
    }

    @Test
    void rejects_null_source() {
        assertThrows(NullPointerException.class, () -> new IncomeEventDraft(
            "inc-synth-001",
            new BigDecimal("75000.00"),
            null,
            LocalDate.of(2026, 3, 1)
        ));
    }

    @Test
    void rejects_negative_amount() {
        assertThrows(IllegalArgumentException.class, () -> new IncomeEventDraft(
            "inc-synth-001",
            new BigDecimal("-1.00"),
            "WAGES",
            LocalDate.of(2026, 3, 1)
        ));
    }

    @Test
    void equal_instances_have_equal_hashcodes() {
        IncomeEventDraft a = new IncomeEventDraft(
            "inc-synth-001", new BigDecimal("75000.00"), "WAGES", LocalDate.of(2026, 3, 1)
        );
        IncomeEventDraft b = new IncomeEventDraft(
            "inc-synth-001", new BigDecimal("75000.00"), "WAGES", LocalDate.of(2026, 3, 1)
        );
        assertEquals(a, b);
        assertEquals(a.hashCode(), b.hashCode());
    }
}
