package com.uptimecrew.tax_liability.llm;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

import com.uptimecrew.tax_liability.graphql.TaxpayerSummary;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel.EmbeddedLiability;
import com.uptimecrew.tax_liability.service.TaxLiabilityService;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

/**
 * Covers the deterministic stub behind the {@code summarizeTaxpayer} GraphQL mutation (W3 D4
 * Task 1); Task 3 of this deliverable replaces the body with a real Spring AI call.
 */
@ExtendWith(MockitoExtension.class)
class LlmSummaryServiceTest {

    @Mock
    TaxLiabilityService service;

    @Test
    void summarize_sums_liabilities_and_counts_distinct_brackets() {
        TaxpayerReadModel taxpayer = new TaxpayerReadModel("taxpayer-001", "Ada Lovelace", "SINGLE", "FEDERAL",
                Instant.now(), List.of(
                        new EmbeddedLiability(2024, "fed-bracket-22", new BigDecimal("100000.00"),
                                new BigDecimal("22000.00"), Instant.now()),
                        new EmbeddedLiability(2023, "fed-bracket-12", new BigDecimal("80000.00"),
                                new BigDecimal("9600.00"), Instant.now())));
        when(service.findById("taxpayer-001")).thenReturn(Optional.of(taxpayer));

        TaxpayerSummary summary = new LlmSummaryService(service).summarize("taxpayer-001");

        assertThat(summary.filingStatus()).isEqualTo("SINGLE");
        assertThat(summary.totalLiability()).isEqualTo(31600.00);
        assertThat(summary.jurisdictionCount()).isEqualTo(2);
    }

    @Test
    void summarize_throws_for_an_unknown_id() {
        when(service.findById("missing")).thenReturn(Optional.empty());

        assertThrows(IllegalArgumentException.class, () -> new LlmSummaryService(service).summarize("missing"));
    }

    @Test
    void summarize_reports_low_risk_below_ten_thousand() {
        assertThat(summarizeWithLiability("4999.99").riskBand()).isEqualTo("LOW");
    }

    @Test
    void summarize_reports_medium_risk_at_or_above_ten_thousand() {
        assertThat(summarizeWithLiability("10000.00").riskBand()).isEqualTo("MEDIUM");
    }

    @Test
    void summarize_reports_high_risk_at_or_above_fifty_thousand() {
        assertThat(summarizeWithLiability("50000.00").riskBand()).isEqualTo("HIGH");
    }

    private TaxpayerSummary summarizeWithLiability(String liabilityAmount) {
        TaxpayerReadModel taxpayer = new TaxpayerReadModel("taxpayer-001", "Ada Lovelace", "SINGLE", "FEDERAL",
                Instant.now(), List.of(new EmbeddedLiability(2024, "fed-bracket-22", new BigDecimal("100000.00"),
                        new BigDecimal(liabilityAmount), Instant.now())));
        when(service.findById("taxpayer-001")).thenReturn(Optional.of(taxpayer));

        return new LlmSummaryService(service).summarize("taxpayer-001");
    }
}
