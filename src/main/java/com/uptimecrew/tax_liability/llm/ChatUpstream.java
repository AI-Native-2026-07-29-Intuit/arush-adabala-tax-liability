package com.uptimecrew.tax_liability.llm;

import com.uptimecrew.tax_liability.llm.cost.UpstreamResponse;

/**
 * The provider boundary for a chat completion: given a prompt and a model id, return the text and
 * the token counts the call billed.
 *
 * <p>Extracted in W6 D5 so the completion path has more than one implementation. Until then
 * {@link AnthropicChatUpstream} was injected by its concrete type, which was fine while it was
 * the only one. Task 3 needs a second - see {@link SyntheticChatUpstream} - and the important
 * property is that the alternative enters through the same seam the real one does, so everything
 * downstream of here ({@link com.uptimecrew.tax_liability.llm.cost.CostMiddleware}, the price
 * book, the cost log, the {@code X-Cost-Usd} header) is byte-for-byte the same code on both
 * paths. A load test that exercised a *parallel* cost path would prove nothing about the one that
 * bills real money.
 *
 * <p>Note this is a different seam from
 * {@link com.uptimecrew.tax_liability.llm.cost.LlmUpstream}, which is the callback
 * {@code CostMiddleware} invokes and is deliberately context-shaped rather than prompt-shaped.
 * This interface is "which provider", that one is "call it now".
 */
public interface ChatUpstream {

    /**
     * Call the model and report what it billed.
     *
     * @param prompt  the user prompt; never null or blank
     * @param modelId bare provider model id, e.g. {@code claude-haiku-4-5}; never null or blank
     * @return the completed call and its billed token counts
     * @throws NullPointerException     if either argument is null
     * @throws IllegalArgumentException if either argument is blank
     * @throws IllegalStateException    if the provider call fails
     */
    UpstreamResponse complete(String prompt, String modelId);
}
