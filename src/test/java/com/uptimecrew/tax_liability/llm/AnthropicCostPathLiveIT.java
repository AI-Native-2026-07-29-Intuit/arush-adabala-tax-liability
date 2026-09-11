package com.uptimecrew.tax_liability.llm;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;

import com.uptimecrew.tax_liability.llm.cost.CallContext;
import com.uptimecrew.tax_liability.llm.cost.CostLogger;
import com.uptimecrew.tax_liability.llm.cost.CostMiddleware;
import com.uptimecrew.tax_liability.llm.cost.PriceBook;
import com.uptimecrew.tax_liability.llm.cost.UpstreamResponse;

import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.ai.anthropic.AnthropicChatModel;
import org.springframework.ai.anthropic.AnthropicChatOptions;
import org.springframework.ai.anthropic.api.AnthropicApi;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.mock.web.MockHttpServletResponse;

/**
 * The cost path against the REAL Anthropic API, with real tokens and real money (W6 D4 Task 2).
 *
 * <p>Every other test in this package stubs the provider, and a stub cannot falsify the two
 * assumptions this whole feature rests on: that the model id in {@link PriceBook} is one the API
 * accepts, and that the provider returns a usage block in the shape the adapter reads. A stub
 * echoes back whatever it was handed, so both assumptions hold by construction there and are
 * tested nowhere. This is the test that can actually fail if either is wrong.
 *
 * <p><b>It spends real money</b> - a fraction of a cent per run, capped hard by
 * {@code max-tokens: 64} on a deliberately trivial prompt. That is the point rather than a
 * regrettable side effect: the number this asserts on is the number the invoice will contain.
 *
 * <p><b>Gated on {@code ANTHROPIC_API_KEY} being present in the environment</b>, so a CI run
 * without a key skips rather than fails. The key is never committed, never written to a file and
 * never logged - it reaches Spring AI from the environment, which in the cluster is a Kubernetes
 * Secret via {@code secretKeyRef} and locally is the shell.
 *
 * <p>Run it explicitly:
 * <pre>{@code
 * ANTHROPIC_API_KEY=sk-ant-... ./gradlew test --tests '*AnthropicCostPathLiveIT'
 * }</pre>
 */
@EnabledIfEnvironmentVariable(named = "ANTHROPIC_API_KEY", matches = ".+",
        disabledReason = "no ANTHROPIC_API_KEY in the environment - this test calls the real, paid API")
class AnthropicCostPathLiveIT {

    /** Hard cap on what one run can cost. A trivial prompt plus 64 output tokens is < $0.001. */
    private static final int MAX_TOKENS = 64;

    @Test
    void realCallIsPricedLoggedAndReturnedInTheHeader() {
        AnthropicApi api = AnthropicApi.builder()
                .apiKey(System.getenv("ANTHROPIC_API_KEY"))
                .build();
        AnthropicChatModel model = AnthropicChatModel.builder()
                .anthropicApi(api)
                .defaultOptions(AnthropicChatOptions.builder()
                        .model(LiabilityExplanationService.MODEL)
                        .maxTokens(MAX_TOKENS)
                        .build())
                .build();

        AnthropicChatUpstream upstream = new AnthropicChatUpstream(ChatClient.builder(model));
        CostMiddleware middleware = new CostMiddleware(new CostLogger());
        MockHttpServletResponse response = new MockHttpServletResponse();
        CallContext ctx = new CallContext(Instant.now(), "taxcalc", "live-it",
                LiabilityExplanationService.FEATURE, response);

        final UpstreamResponse resp;
        try {
            resp = middleware.observe(ctx,
                    c -> upstream.complete("Reply with exactly: ok", LiabilityExplanationService.MODEL));
        } catch (RuntimeException ex) {
            abortIfWorkspaceSpendCapped(ex);
            throw ex;
        }

        // 1. The model id in PriceBook is one the real API accepts. If it were not, the call
        //    above would have thrown before reaching here.
        assertThat(resp.modelId()).isEqualTo(LiabilityExplanationService.MODEL);
        assertThat(PriceBook.knownModels()).contains(resp.modelId());

        // 2. Real usage tokens came back. Non-zero on both sides is the assertion that matters:
        //    a provider returning no usage block would default both to 0 and silently produce a
        //    free-looking paid call, which is the exact failure this feature exists to prevent.
        assertThat(resp.inputTokens()).isPositive();
        assertThat(resp.outputTokens()).isPositive();
        assertThat(resp.outputTokens()).isLessThanOrEqualTo(MAX_TOKENS);

        // 3. The alias/snapshot split, observed live rather than assumed. The API resolves the
        //    floating alias `claude-haiku-4-5` to a dated snapshot; pricing keys off the alias,
        //    the snapshot is recorded for later reconciliation against an invoice.
        assertThat(resp.resolvedModelId()).startsWith(LiabilityExplanationService.MODEL);
        assertThat(resp.servedByDifferentSnapshot())
                .as("the API resolves the alias to a dated snapshot; if this ever stops being "
                        + "true the finding in UpstreamResponse's javadoc has gone stale")
                .isTrue();

        // 4. The header carries a real, non-zero, plain-decimal cost - the value the W6 D5 k6
        //    threshold reads. Exponent-free is the property asserted, since Double.toString
        //    would render this magnitude as scientific notation.
        String header = response.getHeader("X-Cost-Usd");
        assertThat(header).isNotNull().doesNotContainIgnoringCase("e").startsWith("0.");
        assertThat(Double.parseDouble(header)).isPositive();

        System.out.printf("LIVE ANTHROPIC CALL  model=%s resolved=%s in=%d out=%d X-Cost-Usd=%s%n",
                resp.modelId(), resp.resolvedModelId(), resp.inputTokens(), resp.outputTokens(), header);
    }

    /**
     * Skip, rather than fail, when the provider refused the call because the workspace spend cap
     * is exhausted.
     *
     * <p>{@code @EnabledIfEnvironmentVariable} gates this class on the key being <em>present</em>.
     * It cannot gate on the key being <em>usable</em>, and those are different things: a key that
     * exists but has hit its Console workspace limit turns this suite red for a reason that is not
     * a defect in anything this test covers. Observed exactly that:
     *
     * <pre>
     * NonTransientAiException: 400 - {"type":"error","error":{
     *   "type":"invalid_request_error",
     *   "message":"You have reached your specified workspace API usage limits.
     *              You will regain access on 2026-10-01 at 00:00 UTC."}}
     * </pre>
     *
     * <p>There is a pleasing symmetry in that being the thing that broke the build: the workspace
     * spend limit is precisely the LLM-plane guardrail {@code COST.md} names as the cap for this
     * spend, because no AWS Budget can see it. It worked. The test simply could not tell
     * "the guardrail stopped me" apart from "the code is wrong".
     *
     * <p><b>Matched on the message, deliberately, and not on the error type.</b>
     * {@code invalid_request_error} is also what a bad model id or a malformed request returns —
     * which is exactly what this test exists to catch, and must keep catching. Only the
     * spend-cap wording aborts; every other failure rethrows unchanged.
     *
     * <p>What goes unverified while the cap is in force is narrow and worth stating: the model id
     * being one the API accepts, and the token accounting. The stubbed path
     * ({@code LlmProxyCompletionsIT}) still covers the header, the EMF line and the cost
     * arithmetic end to end through the HTTP stack.
     *
     * <p>Kept identical to {@code LlmProxyLiveCostIT}'s helper on purpose — one convention for
     * "the platform cap refused us", so a reader who has met it once recognises it here.
     */
    private static void abortIfWorkspaceSpendCapped(Throwable ex) {
        for (Throwable cause = ex; cause != null; cause = cause.getCause()) {
            String message = cause.getMessage();
            if (message != null && message.contains("workspace API usage limits")) {
                Assumptions.abort("Anthropic workspace spend limit is in force - the Task 2 platform "
                        + "cap refused the call before any tokens were bought. Raise the limit in the "
                        + "Console workspace, or re-run after it resets. Provider said: " + message);
            }
            if (cause.getCause() == cause) {
                break;
            }
        }
    }
}
