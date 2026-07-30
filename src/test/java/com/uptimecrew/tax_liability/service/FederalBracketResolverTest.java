package com.uptimecrew.tax_liability.service;

import java.math.BigDecimal;

import com.uptimecrew.tax_liability.model.TaxBracket;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

class FederalBracketResolverTest {

    @Test
    void resolves_representative_amount_to_expected_bracket() {
        BracketResolver subject = new FederalBracketResolver();

        TaxBracket resolved = subject.resolve(new BigDecimal("75000.00"));

        assertNotNull(resolved);
        assertEquals("fed-bracket-22", resolved.getId());
        assertEquals("FEDERAL", resolved.getJurisdiction());
    }

    @Test
    void resolves_amount_above_highest_floor_to_open_top_bracket() {
        BracketResolver subject = new FederalBracketResolver();

        TaxBracket resolved = subject.resolve(new BigDecimal("500000.00"));

        assertNotNull(resolved);
        assertEquals("fed-bracket-24", resolved.getId());
    }
}
