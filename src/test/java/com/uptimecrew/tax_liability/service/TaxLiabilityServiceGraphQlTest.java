package com.uptimecrew.tax_liability.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.List;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.outbox.EventOutboxRepository;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;
import com.uptimecrew.tax_liability.repository.TaxpayerRepository;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.PageRequest;

/**
 * Covers {@link TaxLiabilityService#findLatest} (W3 D4 Task 1), the new method backing the
 * GraphQL {@code latestTaxpayers} query.
 */
@ExtendWith(MockitoExtension.class)
class TaxLiabilityServiceGraphQlTest {

    @Mock
    BracketResolver strategy;

    @Mock
    TaxpayerRepository repository;

    @Mock
    TaxpayerReadModelRepository readModelRepository;

    @Mock
    EventOutboxRepository outboxRepository;

    ObjectMapper objectMapper = new ObjectMapper().findAndRegisterModules();

    private TaxLiabilityService subject() {
        return new TaxLiabilityService(strategy, repository, readModelRepository, outboxRepository, objectMapper);
    }

    @Test
    void findLatest_delegates_to_read_model_repository_ordered_by_created_at_descending() {
        TaxpayerReadModel newest = readModel("newest");
        when(readModelRepository.findAll(any(PageRequest.class)))
                .thenReturn(new PageImpl<>(List.of(newest)));

        List<TaxpayerReadModel> result = subject().findLatest(5);

        assertThat(result).containsExactly(newest);
        verify(readModelRepository).findAll(PageRequest.of(0, 5,
                org.springframework.data.domain.Sort.by(org.springframework.data.domain.Sort.Direction.DESC, "createdAt")));
    }

    @Test
    void findLatest_rejects_non_positive_limit() {
        assertThrows(IllegalArgumentException.class, () -> subject().findLatest(0));
    }

    private TaxpayerReadModel readModel(String id) {
        return new TaxpayerReadModel(id, "Ada Lovelace", "SINGLE", "FEDERAL", java.time.Instant.now(), List.of());
    }
}
