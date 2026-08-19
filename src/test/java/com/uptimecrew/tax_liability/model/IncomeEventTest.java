package com.uptimecrew.tax_liability.model;

import java.math.BigDecimal;
import java.time.LocalDate;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * Covers {@link IncomeEvent}'s constructor validation and {@code equals}/{@code hashCode}.
 */
class IncomeEventTest {

    @Test
    void constructs_with_valid_inputs() {
        IncomeEvent subject = new IncomeEvent(
                "inc-synth-001",
                "taxpayer-001",
                IncomeSource.WAGES,
                new BigDecimal("75000.00"),
                LocalDate.of(2026, 3, 1)
        );
        assertEquals("inc-synth-001", subject.getId());
        assertEquals("taxpayer-001", subject.getTaxpayerId());
        assertEquals(IncomeSource.WAGES, subject.getSource());
        assertEquals(0, new BigDecimal("75000.00").compareTo(subject.getAmount()));
        assertEquals(LocalDate.of(2026, 3, 1), subject.getOccurredOn());
    }

    @Test
    void rejects_null_taxpayer_id() {
        assertThrows(NullPointerException.class, () -> new IncomeEvent(
                "inc-synth-001",
                null,
                IncomeSource.WAGES,
                new BigDecimal("75000.00"),
                LocalDate.of(2026, 3, 1)
        ));
    }

    @Test
    void rejects_negative_amount() {
        assertThrows(IllegalArgumentException.class, () -> new IncomeEvent(
                "inc-synth-001",
                "taxpayer-001",
                IncomeSource.WAGES,
                new BigDecimal("-1.00"),
                LocalDate.of(2026, 3, 1)
        ));
    }

    @Test
    void rejects_blank_id() {
        assertThrows(IllegalArgumentException.class, () -> new IncomeEvent(
                "  ",
                "taxpayer-001",
                IncomeSource.WAGES,
                new BigDecimal("75000.00"),
                LocalDate.of(2026, 3, 1)
        ));
    }

    @Test
    void equal_instances_have_equal_hashcodes() {
        IncomeEvent a = new IncomeEvent(
                "inc-synth-001", "taxpayer-001", IncomeSource.WAGES, new BigDecimal("75000.00"), LocalDate.of(2026, 3, 1)
        );
        IncomeEvent b = new IncomeEvent(
                "inc-synth-001", "taxpayer-001", IncomeSource.WAGES, new BigDecimal("75000.00"), LocalDate.of(2026, 3, 1)
        );
        assertEquals(a, b);
        assertEquals(a.hashCode(), b.hashCode());
    }
}
