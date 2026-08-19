package com.uptimecrew.tax_liability.llm;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.graphql.TaxpayerSummary;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.ai.chat.client.ChatClient;

/**
 * Covers {@link LlmSummaryService} (W3 D4 Task 3) without hitting Anthropic: the whole
 * {@code ChatClient} fluent chain is mocked, so these tests exercise the service's own logic -
 * looking up the read model, invoking the structured-output call, and re-validating the result
 * against the JSON Schema - deterministically.
 */
@ExtendWith(MockitoExtension.class)
class LlmSummaryServiceTest {

    @Mock
    TaxpayerReadModelRepository readModelRepository;

    @Mock
    ChatClient.Builder builder;

    @Mock
    ChatClient chatClient;

    @Mock
    ChatClient.ChatClientRequestSpec requestSpec;

    @Mock
    ChatClient.CallResponseSpec callResponseSpec;

    ObjectMapper mapper = new ObjectMapper().findAndRegisterModules();

    @BeforeEach
    void wireChatClient() {
        when(builder.build()).thenReturn(chatClient);
    }

    private LlmSummaryService subject() {
        return new LlmSummaryService(builder, readModelRepository, mapper);
    }

    private void stubChatClientToReturn(TaxpayerSummary response) {
        when(chatClient.prompt()).thenReturn(requestSpec);
        when(requestSpec.user(anyString())).thenReturn(requestSpec);
        when(requestSpec.call()).thenReturn(callResponseSpec);
        when(callResponseSpec.entity(TaxpayerSummary.class)).thenReturn(response);
    }

    @Test
    void summarize_returns_the_structured_output_when_it_matches_the_schema() {
        when(readModelRepository.findById("taxpayer-001")).thenReturn(Optional.of(readModel("taxpayer-001")));
        TaxpayerSummary expected = new TaxpayerSummary("SINGLE", 6600.0, 1, "MEDIUM");
        stubChatClientToReturn(expected);

        TaxpayerSummary result = subject().summarize("taxpayer-001");

        assertThat(result).isEqualTo(expected);
    }

    @Test
    void summarize_throws_for_an_unknown_id() {
        when(readModelRepository.findById("missing")).thenReturn(Optional.empty());

        assertThrows(IllegalArgumentException.class, () -> subject().summarize("missing"));
    }

    @Test
    void summarize_rejects_a_structured_output_whose_riskBand_is_outside_the_schemas_enum() {
        when(readModelRepository.findById("taxpayer-001")).thenReturn(Optional.of(readModel("taxpayer-001")));
        stubChatClientToReturn(new TaxpayerSummary("SINGLE", 6600.0, 1, "EXTREME"));

        assertThrows(IllegalStateException.class, () -> subject().summarize("taxpayer-001"));
    }

    @Test
    void summarize_rejects_a_structured_output_with_a_negative_total_liability() {
        when(readModelRepository.findById("taxpayer-001")).thenReturn(Optional.of(readModel("taxpayer-001")));
        stubChatClientToReturn(new TaxpayerSummary("SINGLE", -100.0, 1, "LOW"));

        assertThrows(IllegalStateException.class, () -> subject().summarize("taxpayer-001"));
    }

    private TaxpayerReadModel readModel(String id) {
        return new TaxpayerReadModel(id, "Ada Lovelace", "SINGLE", "FEDERAL", Instant.now(), List.of());
    }
}
