package com.uptimecrew.tax_liability.service;

import java.math.BigDecimal;
import java.util.Optional;

import com.uptimecrew.tax_liability.model.TaxBracket;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TaxLiabilityServiceMockitoTest {

    @Mock
    BracketResolver strategy;

    @Test
    void delegates_to_injected_strategy_and_applies_its_rate() {
        BigDecimal taxableAmount = new BigDecimal("75000.00");
        TaxBracket resolvedBracket = new TaxBracket(
                "fed-bracket-22", "FEDERAL", new BigDecimal("0.22"), new BigDecimal("47150"), new BigDecimal("100525"));
        when(strategy.resolve(any())).thenReturn(Optional.of(resolvedBracket));

        TaxLiabilityService subject = new TaxLiabilityService(strategy);
        BigDecimal liability = subject.computeLiability(taxableAmount);

        verify(strategy).resolve(taxableAmount);
        assertEquals(new BigDecimal("16500.00"), liability);
    }

    @Test
    void throws_when_injected_strategy_resolves_no_bracket() {
        when(strategy.resolve(any())).thenReturn(Optional.empty());

        TaxLiabilityService subject = new TaxLiabilityService(strategy);

        assertThrows(IllegalStateException.class, () -> subject.computeLiability(new BigDecimal("1000.00")));
    }
}
