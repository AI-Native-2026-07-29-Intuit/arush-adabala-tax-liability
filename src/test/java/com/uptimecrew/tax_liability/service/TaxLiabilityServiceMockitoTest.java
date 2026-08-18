package com.uptimecrew.tax_liability.service;

import java.math.BigDecimal;
import java.util.Optional;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.entity.Taxpayer;
import com.uptimecrew.tax_liability.model.TaxBracket;
import com.uptimecrew.tax_liability.outbox.EventOutboxRepository;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;
import com.uptimecrew.tax_liability.repository.TaxpayerRepository;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static com.uptimecrew.tax_liability.model.TaxBracketTestDataBuilder.aTaxBracket;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TaxLiabilityServiceMockitoTest {

    @Mock
    BracketResolver strategy;

    @Mock
    TaxpayerRepository repository;

    @Mock
    TaxpayerReadModelRepository readModelRepository;

    @Mock
    EventOutboxRepository outboxRepository;

    ObjectMapper objectMapper = new ObjectMapper().findAndRegisterModules();

    @Test
    void delegates_to_injected_strategy_and_saves_taxpayer_under_resolved_jurisdiction() {
        BigDecimal taxableAmount = new BigDecimal("75000.00");
        TaxBracket resolvedBracket = aTaxBracket()
                .withId("fed-bracket-22").withJurisdiction("FEDERAL")
                .withRate(new BigDecimal("0.22")).withFloor(new BigDecimal("47150")).withCeiling(new BigDecimal("100525"))
                .build();
        when(strategy.resolve(any())).thenReturn(Optional.of(resolvedBracket));
        when(repository.save(any())).thenAnswer(inv -> inv.getArgument(0));

        TaxLiabilityService subject =
                new TaxLiabilityService(strategy, repository, readModelRepository, outboxRepository, objectMapper);
        Taxpayer saved = subject.computeLiability("taxpayer-001", "Ada Lovelace", "SINGLE", taxableAmount);

        verify(strategy).resolve(taxableAmount);
        verify(readModelRepository).save(any());
        verify(outboxRepository).save(any());
        assertEquals("taxpayer-001", saved.getId());
        assertEquals("Ada Lovelace", saved.getDisplayName());
        assertEquals("SINGLE", saved.getFilingStatus());
        assertEquals("FEDERAL", saved.getHomeJurisdiction());
    }

    @Test
    void throws_when_injected_strategy_resolves_no_bracket() {
        when(strategy.resolve(any())).thenReturn(Optional.empty());

        TaxLiabilityService subject =
                new TaxLiabilityService(strategy, repository, readModelRepository, outboxRepository, objectMapper);

        assertThrows(IllegalStateException.class,
                () -> subject.computeLiability("taxpayer-001", "Ada Lovelace", "SINGLE", new BigDecimal("1000.00")));
    }
}
