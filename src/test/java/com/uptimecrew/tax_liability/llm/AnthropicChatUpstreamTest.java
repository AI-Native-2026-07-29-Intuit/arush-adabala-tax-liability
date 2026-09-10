package com.uptimecrew.tax_liability.llm;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.List;

import com.uptimecrew.tax_liability.llm.cost.UpstreamResponse;

import org.junit.jupiter.api.Test;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.messages.AssistantMessage;
import org.springframework.ai.chat.metadata.ChatResponseMetadata;
import org.springframework.ai.chat.metadata.DefaultUsage;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.model.Generation;
import org.springframework.ai.chat.prompt.Prompt;

/**
 * {@link AnthropicChatUpstream}'s adaptation of a provider response into {@link UpstreamResponse}
 * (W6 D4 Task 2), with the provider stubbed.
 *
 * <p>{@link AnthropicCostPathLiveIT} covers the real call but skips without an API key - i.e. on
 * every CI run - so the mapping this class performs would otherwise be untested precisely where
 * it would regress unnoticed. What a stub genuinely can pin: the guard clauses, the usage-token
 * mapping, and the null-usage default. What it deliberately cannot is the alias/snapshot
 * distinction, since a stub echoes back whatever it is handed - which is exactly why the live
 * test exists alongside this one.
 */
class AnthropicChatUpstreamTest {

    private static final String MODEL = "claude-haiku-4-5";

    private static AnthropicChatUpstream upstreamReturning(String text, Integer in, Integer out, String model) {
        ChatModel stub = mock(ChatModel.class);
        ChatResponseMetadata.Builder metadata = ChatResponseMetadata.builder();
        if (in != null || out != null) {
            metadata.usage(new DefaultUsage(in == null ? 0 : in, out == null ? 0 : out));
        }
        if (model != null) {
            metadata.model(model);
        }
        ChatResponse response = new ChatResponse(
                List.of(new Generation(new AssistantMessage(text))), metadata.build());
        when(stub.call(any(Prompt.class))).thenReturn(response);
        return new AnthropicChatUpstream(ChatClient.builder(stub));
    }

    @Test
    void mapsProviderUsageTokensOntoTheResponse() {
        AnthropicChatUpstream upstream = upstreamReturning("an explanation", 120, 40, MODEL);

        UpstreamResponse response = upstream.complete("why do I owe this?", MODEL);

        assertThat(response.modelId()).isEqualTo(MODEL);
        assertThat(response.inputTokens()).isEqualTo(120);
        assertThat(response.outputTokens()).isEqualTo(40);
        assertThat(response.totalTokens()).isEqualTo(160);
        assertThat(response.success()).isTrue();
        assertThat(response.text()).isEqualTo("an explanation");
        assertThat(response.latencyMs()).isNotNegative();
    }

    /**
     * A provider reporting a dated snapshot must not overwrite the requested alias - the alias is
     * the pricing key, and pricing off the snapshot throws. The live test proves the API really
     * does this; this proves the mapping keeps them separate when it happens.
     */
    @Test
    void keepsTheRequestedAliasSeparateFromTheReportedSnapshot() {
        AnthropicChatUpstream upstream =
                upstreamReturning("text", 10, 5, "claude-haiku-4-5-20251001");

        UpstreamResponse response = upstream.complete("prompt", MODEL);

        assertThat(response.modelId()).isEqualTo(MODEL);
        assertThat(response.resolvedModelId()).isEqualTo("claude-haiku-4-5-20251001");
        assertThat(response.servedByDifferentSnapshot()).isTrue();
    }

    /** No reported model id degrades the audit trail rather than failing the call. */
    @Test
    void fallsBackToTheRequestedIdWhenNoModelIsReported() {
        AnthropicChatUpstream upstream = upstreamReturning("text", 10, 5, null);

        UpstreamResponse response = upstream.complete("prompt", MODEL);

        assertThat(response.resolvedModelId()).isEqualTo(MODEL);
        assertThat(response.servedByDifferentSnapshot()).isFalse();
    }

    /**
     * A provider returning no usage block defaults both counts to 0 - which surfaces as a
     * zero-cost call in the cost series rather than as a plausible guess that quietly disagrees
     * with the invoice.
     */
    @Test
    void defaultsMissingUsageToZeroRatherThanEstimating() {
        AnthropicChatUpstream upstream = upstreamReturning("text", null, null, MODEL);

        UpstreamResponse response = upstream.complete("prompt", MODEL);

        assertThat(response.inputTokens()).isZero();
        assertThat(response.outputTokens()).isZero();
    }

    @Test
    void rejectsBlankPromptOrModel() {
        AnthropicChatUpstream upstream = upstreamReturning("text", 1, 1, MODEL);

        assertThatThrownBy(() -> upstream.complete("  ", MODEL))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("prompt");
        assertThatThrownBy(() -> upstream.complete("prompt", "  "))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("modelId");
    }

    @Test
    void rejectsNullArguments() {
        AnthropicChatUpstream upstream = upstreamReturning("text", 1, 1, MODEL);

        assertThrows(NullPointerException.class, () -> upstream.complete(null, MODEL));
        assertThrows(NullPointerException.class, () -> upstream.complete("prompt", null));
        assertThrows(NullPointerException.class, () -> new AnthropicChatUpstream(null));
    }

    /**
     * A provider failure is rethrown rather than folded into a {@code success=false} response: a
     * call that never reached the provider billed nothing, and recording it as a zero-cost success
     * would add a fake datapoint to the cost series.
     */
    @Test
    void rethrowsProviderFailuresInsteadOfRecordingAZeroCostCall() {
        ChatModel stub = mock(ChatModel.class);
        when(stub.call(any(Prompt.class))).thenThrow(new IllegalStateException("upstream exploded"));
        AnthropicChatUpstream upstream = new AnthropicChatUpstream(ChatClient.builder(stub));

        assertThatThrownBy(() -> upstream.complete("prompt", MODEL))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("upstream exploded");
    }
}
