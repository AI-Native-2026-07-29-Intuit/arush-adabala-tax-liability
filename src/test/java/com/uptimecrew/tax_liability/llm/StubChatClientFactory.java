package com.uptimecrew.tax_liability.llm;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.List;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.graphql.TaxpayerSummary;

import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.messages.AssistantMessage;
import org.springframework.ai.chat.metadata.ChatResponseMetadata;
import org.springframework.ai.chat.metadata.DefaultUsage;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.model.Generation;
import org.springframework.ai.chat.prompt.Prompt;

/**
 * Test-only factory for a deterministic, Anthropic-free {@link ChatClient.Builder} (W3 D5):
 * previously {@code TaxpayerGraphQlIT} and {@code TaxpayerObservabilityIT} each hand-rolled their
 * own copy of the same {@link ChatModel} mock; this extracts the shared shape so both - and any
 * future IT stubbing the LLM path - build it the same way instead of drifting apart.
 *
 * <p>{@link #builderReturning} mocks {@link ChatModel#call(Prompt)} to return the given {@link
 * TaxpayerSummary} as the assistant's JSON text, wrapped in a real {@link ChatResponse} carrying
 * real token-usage metadata - callers still exercise Spring AI's actual structured-output parsing
 * and {@code LlmSummaryService}'s OTel span attribute wiring, just without a network call.
 */
public final class StubChatClientFactory {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private StubChatClientFactory() {
        throw new AssertionError("StubChatClientFactory is not instantiable");
    }

    /** Non-zero, arbitrary token counts (17 in / 42 out) - enough to prove real propagation. */
    public static ChatClient.Builder builderReturning(TaxpayerSummary summary) {
        return builderReturning(summary, 17, 42);
    }

    public static ChatClient.Builder builderReturning(TaxpayerSummary summary, int promptTokens,
            int completionTokens) {
        ChatModel stub = mock(ChatModel.class);
        ChatResponse response = new ChatResponse(List.of(new Generation(new AssistantMessage(toJson(summary)))),
                ChatResponseMetadata.builder().usage(new DefaultUsage(promptTokens, completionTokens)).build());
        when(stub.call(any(Prompt.class))).thenReturn(response);
        return ChatClient.builder(stub);
    }

    private static String toJson(TaxpayerSummary summary) {
        try {
            return MAPPER.writeValueAsString(summary);
        } catch (JsonProcessingException ex) {
            throw new IllegalStateException("failed to serialize stub TaxpayerSummary", ex);
        }
    }
}
