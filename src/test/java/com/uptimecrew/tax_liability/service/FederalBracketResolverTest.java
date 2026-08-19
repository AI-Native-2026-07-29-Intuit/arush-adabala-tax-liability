package com.uptimecrew.tax_liability.service;

import java.math.BigDecimal;
import java.util.Optional;

import com.uptimecrew.tax_liability.exception.InvalidIncomeException;
import com.uptimecrew.tax_liability.model.TaxBracket;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Covers {@link FederalBracketResolver}: resolving into the expected marginal bracket, falling
 * into the open-ended top bracket, and rejecting a negative taxable amount.
 */
class FederalBracketResolverTest {

    @Test
    void resolves_representative_amount_to_expected_bracket() {
        BracketResolver subject = new FederalBracketResolver();

        Optional<TaxBracket> resolved = subject.resolve(new BigDecimal("75000.00"));

        assertTrue(resolved.isPresent());
        assertEquals("fed-bracket-22", resolved.orElseThrow().id());
        assertEquals("FEDERAL", resolved.orElseThrow().jurisdiction());
    }

    @Test
    void resolves_amount_above_highest_floor_to_open_top_bracket() {
        BracketResolver subject = new FederalBracketResolver();

        Optional<TaxBracket> resolved = subject.resolve(new BigDecimal("500000.00"));

        assertTrue(resolved.isPresent());
        assertEquals("fed-bracket-24", resolved.orElseThrow().id());
    }

    @Test
    void throws_invalid_income_exception_for_negative_amount() {
        BracketResolver subject = new FederalBracketResolver();

        assertThrows(InvalidIncomeException.class, () -> subject.resolve(new BigDecimal("-100.00")));
    }
}
