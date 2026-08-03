package com.uptimecrew.tax_liability.service;

import java.math.BigDecimal;
import java.util.Optional;

import com.uptimecrew.tax_liability.model.TaxBracket;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FlatStateBracketResolverTest {

    @Test
    void resolves_any_non_negative_amount_to_the_flat_rate_bracket() {
        BracketResolver subject = new FlatStateBracketResolver("COLORADO", new BigDecimal("0.044"));

        Optional<TaxBracket> resolved = subject.resolve(new BigDecimal("9999999.00"));

        assertTrue(resolved.isPresent());
        assertEquals("COLORADO", resolved.orElseThrow().jurisdiction());
        assertEquals(0, new BigDecimal("0.044").compareTo(resolved.orElseThrow().rate()));
    }

    @Test
    void rejects_negative_rate() {
        assertThrows(IllegalArgumentException.class,
                () -> new FlatStateBracketResolver("COLORADO", new BigDecimal("-0.01")));
    }

    @Test
    void rejects_negative_taxable_amount() {
        BracketResolver subject = new FlatStateBracketResolver("COLORADO", new BigDecimal("0.044"));

        assertThrows(IllegalArgumentException.class, () -> subject.resolve(new BigDecimal("-1")));
    }
}
