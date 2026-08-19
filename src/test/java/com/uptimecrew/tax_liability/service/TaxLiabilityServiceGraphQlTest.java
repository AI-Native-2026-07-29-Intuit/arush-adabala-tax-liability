package com.uptimecrew.tax_liability.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.graphql.LineItem;
import com.uptimecrew.tax_liability.outbox.EventOutboxRepository;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel.EmbeddedLiability;
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
 * Covers the two W3 D4 additions to {@link TaxLiabilityService}: {@link
 * TaxLiabilityService#findLatest} (backs the GraphQL {@code latestTaxpayers} query) and {@link
 * TaxLiabilityService#loadLineItemsByParent} (the {@code @BatchMapping} N+1 fix for {@code
 * Taxpayer.lines}).
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

    @Test
    void loadLineItemsByParent_groups_each_parents_liabilities_into_line_items() {
        TaxpayerReadModel taxpayer = new TaxpayerReadModel("taxpayer-001", "Ada Lovelace", "SINGLE", "FEDERAL",
                Instant.now(), List.of(
                        new EmbeddedLiability(2024, "fed-bracket-22", new BigDecimal("100000.00"),
                                new BigDecimal("22000.00"), Instant.now()),
                        new EmbeddedLiability(2023, "fed-bracket-12", new BigDecimal("80000.00"),
                                new BigDecimal("9600.00"), Instant.now())));

        Map<TaxpayerReadModel, List<LineItem>> result = subject().loadLineItemsByParent(List.of(taxpayer));

        assertThat(result).containsOnlyKeys(taxpayer);
        assertThat(result.get(taxpayer))
                .extracting(LineItem::id, LineItem::amount)
                .containsExactlyInAnyOrder(
                        org.assertj.core.groups.Tuple.tuple("taxpayer-001-2024", 22000.00),
                        org.assertj.core.groups.Tuple.tuple("taxpayer-001-2023", 9600.00));
    }

    @Test
    void loadLineItemsByParent_returns_empty_line_items_for_a_taxpayer_with_no_liabilities() {
        TaxpayerReadModel taxpayer = readModel("no-liabilities");

        Map<TaxpayerReadModel, List<LineItem>> result = subject().loadLineItemsByParent(List.of(taxpayer));

        assertThat(result.get(taxpayer)).isEmpty();
    }

    private TaxpayerReadModel readModel(String id) {
        return new TaxpayerReadModel(id, "Ada Lovelace", "SINGLE", "FEDERAL", Instant.now(), List.of());
    }
}
