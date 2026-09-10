package com.uptimecrew.tax_liability.llm;

import java.util.Objects;

import com.uptimecrew.tax_liability.llm.cost.UpstreamResponse;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.anthropic.AnthropicChatOptions;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.stereotype.Component;

/**
 * The one class in the cost path that knows which provider is serving the call (W6 D4 Task 2).
 *
 * <p>Everything downstream - {@link com.uptimecrew.tax_liability.llm.cost.CostMiddleware},
 * {@link com.uptimecrew.tax_liability.llm.cost.CostLogger},
 * {@link com.uptimecrew.tax_liability.llm.cost.CostResponseHeader} - is written against
 * {@link com.uptimecrew.tax_liability.llm.cost.LlmUpstream} and never names Anthropic. Pointing
 * the application at a managed inference gateway (Bedrock {@code InvokeModel}) means a sibling of
 * this class and a config change; the token-based accounting and per-feature attribution are
 * identical either side of that boundary, because both read the same two numbers out of the same
 * usage block.
 *
 * <h2>The API key</h2>
 *
 * <p>{@code ANTHROPIC_API_KEY} is resolved from the environment by Spring AI's own
 * auto-configuration ({@code spring.ai.anthropic.api-key} in {@code application.yml} reads the
 * environment variable). In the cluster that variable comes from a Kubernetes Secret via
 * {@code secretKeyRef}; locally it comes from the shell. It appears in no source file, no
 * ConfigMap, no image layer and no committed config, and this class deliberately never reads,
 * logs or holds it - it only ever touches the {@link ChatClient} that was configured with it.
 *
 * <h2>Model ids are bare</h2>
 *
 * <p>{@code claude-haiku-4-5}, with no {@code anthropic.} prefix and no Bedrock {@code -v1:0}
 * suffix. Those decorations belong to Bedrock's model-id namespace; the direct Anthropic API uses
 * the bare form, and {@link com.uptimecrew.tax_liability.llm.cost.PriceBook} is keyed to match.
 */
@Component
public class AnthropicChatUpstream {

    private static final Logger LOG = LoggerFactory.getLogger(AnthropicChatUpstream.class);

    private final ChatClient chatClient;

    /**
     * @param builder Spring AI's configured client builder; never null
     * @throws NullPointerException if {@code builder} is null
     */
    public AnthropicChatUpstream(ChatClient.Builder builder) {
        Objects.requireNonNull(builder, "builder must not be null");
        this.chatClient = builder.build();
    }

    /**
     * Call the model and report what it billed.
     *
     * <p>The per-call {@code model} override is what lets one feature choose Haiku while the
     * application default stays Sonnet. Naming the model at the call site rather than globally is
     * the whole cost lever here: explain-liability is high-volume and short-answer, so it runs on
     * the model that is roughly 3x cheaper per token, and the choice is visible in the diff of
     * the feature that made it rather than buried in shared config.
     *
     * <p>Token counts come from the provider's usage block, never estimated from the prompt. If a
     * provider returns no usage, both default to 0 - which shows up as a zero-cost call in the
     * cost series rather than as a plausible guess that quietly disagrees with the invoice.
     *
     * @param prompt  the user prompt; never null or blank
     * @param modelId bare provider model id, e.g. {@code claude-haiku-4-5}; never null or blank
     * @return the completed call and its billed token counts
     * @throws NullPointerException     if either argument is null
     * @throws IllegalArgumentException if either argument is blank
     * @throws IllegalStateException    if the provider call fails
     */
    public UpstreamResponse complete(String prompt, String modelId) {
        requireText(prompt, "prompt");
        requireText(modelId, "modelId");

        long startedNanos = System.nanoTime();
        try {
            ChatResponse response = chatClient.prompt()
                    .options(AnthropicChatOptions.builder().model(modelId).build())
                    .user(prompt)
                    .call()
                    .chatResponse();

            long latencyMs = elapsedMs(startedNanos);
            if (response == null) {
                throw new IllegalStateException("Anthropic call returned a null ChatResponse");
            }
            String text = response.getResult() == null ? null : response.getResult().getOutput().getText();
            long inputTokens = safeLong(response.getMetadata().getUsage().getPromptTokens());
            long outputTokens = safeLong(response.getMetadata().getUsage().getCompletionTokens());

            // The requested id is a floating ALIAS; the response names the dated SNAPSHOT that
            // served the call - `claude-haiku-4-5` comes back as `claude-haiku-4-5-20251001`,
            // measured against the live API. Both are carried: the alias is the pricing key
            // (PriceBook is keyed on it, and looking up the snapshot would throw), the snapshot
            // is what makes the cost line reconcilable against an invoice later.
            String resolved = response.getMetadata().getModel();

            LOG.debug("anthropic ok model={} resolved={} tokens.in={} tokens.out={} latencyMs={}",
                    modelId, resolved, inputTokens, outputTokens, latencyMs);
            return new UpstreamResponse(modelId, resolved, inputTokens, outputTokens, latencyMs, true, text);
        } catch (RuntimeException ex) {
            // Rethrown, not swallowed into a success=false response: a call that never reached
            // the provider billed nothing, and recording it as a zero-cost success would add a
            // fake datapoint to the cost series. The caller's error path handles it.
            LOG.warn("anthropic call failed model={} latencyMs={} error={}",
                    modelId, elapsedMs(startedNanos), ex.getClass().getSimpleName());
            throw ex;
        }
    }

    private static long elapsedMs(long startedNanos) {
        return (System.nanoTime() - startedNanos) / 1_000_000L;
    }

    private static long safeLong(Number n) {
        return n == null ? 0L : n.longValue();
    }

    private static void requireText(String value, String field) {
        Objects.requireNonNull(value, field + " must not be null");
        if (value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
    }
}
