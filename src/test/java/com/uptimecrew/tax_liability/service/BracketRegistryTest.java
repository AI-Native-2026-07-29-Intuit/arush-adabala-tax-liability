package com.uptimecrew.tax_liability.service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import com.uptimecrew.tax_liability.model.TaxBracket;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class BracketRegistryTest {

    private static final TaxBracket BRACKET_10 = new TaxBracket(
            "fed-bracket-10", "FEDERAL", new BigDecimal("0.10"), new BigDecimal("0"), new BigDecimal("11600"));
    private static final TaxBracket BRACKET_12 = new TaxBracket(
            "fed-bracket-12", "FEDERAL", new BigDecimal("0.12"), new BigDecimal("11600"), new BigDecimal("47150"));
    private static final TaxBracket BRACKET_22 = new TaxBracket(
            "fed-bracket-22", "FEDERAL", new BigDecimal("0.22"), new BigDecimal("47150"), new BigDecimal("100525"));

    @Test
    void size_reflects_number_of_brackets_registered() {
        BracketRegistry subject = new BracketRegistry(List.of(BRACKET_10, BRACKET_12, BRACKET_22));

        assertEquals(3, subject.size());
    }

    @Test
    void findById_returns_matching_bracket() {
        BracketRegistry subject = new BracketRegistry(List.of(BRACKET_10, BRACKET_12));

        Optional<TaxBracket> found = subject.findById("fed-bracket-12");

        assertTrue(found.isPresent());
        assertEquals("fed-bracket-12", found.orElseThrow().getId());
    }

    @Test
    void findById_returns_empty_when_no_bracket_matches() {
        BracketRegistry subject = new BracketRegistry(List.of(BRACKET_10));

        assertFalse(subject.findById("does-not-exist").isPresent());
    }

    @Test
    void constructor_defensively_copies_input_collection() {
        List<TaxBracket> mutableInput = new ArrayList<>(List.of(BRACKET_10));
        BracketRegistry subject = new BracketRegistry(mutableInput);

        mutableInput.add(BRACKET_12);

        assertEquals(1, subject.size());
    }

    @Test
    void constructor_rejects_null_collection() {
        assertThrows(NullPointerException.class, () -> new BracketRegistry(null));
    }

    @Test
    void findByJurisdictionAbove_filters_and_sorts_by_floor_ascending() {
        BracketRegistry subject = new BracketRegistry(List.of(BRACKET_22, BRACKET_10, BRACKET_12));

        List<TaxBracket> found = subject.findByJurisdictionAbove("FEDERAL", new BigDecimal("11600"));

        assertEquals(List.of("fed-bracket-12", "fed-bracket-22"), found.stream().map(TaxBracket::getId).toList());
    }

    @Test
    void findByJurisdictionAbove_excludes_other_jurisdictions() {
        BracketRegistry subject = new BracketRegistry(List.of(BRACKET_10));

        List<TaxBracket> found = subject.findByJurisdictionAbove("STATE", new BigDecimal("0"));

        assertTrue(found.isEmpty());
    }
}
