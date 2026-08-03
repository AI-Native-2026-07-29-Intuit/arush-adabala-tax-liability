package com.uptimecrew.tax_liability.service;

import java.math.BigDecimal;
import java.util.Optional;

import com.uptimecrew.tax_liability.model.TaxBracket;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class NoIncomeTaxStateBracketResolverTest {

    @Test
    void resolves_any_non_negative_amount_to_a_zero_rate_bracket() {
        BracketResolver subject = new NoIncomeTaxStateBracketResolver("TEXAS");

        Optional<TaxBracket> resolved = subject.resolve(new BigDecimal("9999999.00"));

        assertTrue(resolved.isPresent());
        assertEquals("TEXAS", resolved.orElseThrow().jurisdiction());
        assertEquals(0, BigDecimal.ZERO.compareTo(resolved.orElseThrow().rate()));
    }

    @Test
    void rejects_blank_jurisdiction() {
        assertThrows(IllegalArgumentException.class, () -> new NoIncomeTaxStateBracketResolver("  "));
    }

    @Test
    void rejects_negative_taxable_amount() {
        BracketResolver subject = new NoIncomeTaxStateBracketResolver("TEXAS");

        assertThrows(IllegalArgumentException.class, () -> subject.resolve(new BigDecimal("-1")));
    }
}
