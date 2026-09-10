package com.uptimecrew.tax_liability.llm;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;

import com.uptimecrew.tax_liability.llm.cost.CallContext;
import com.uptimecrew.tax_liability.llm.cost.CostLogger;
import com.uptimecrew.tax_liability.llm.cost.CostMiddleware;
import com.uptimecrew.tax_liability.llm.cost.PriceBook;
import com.uptimecrew.tax_liability.llm.cost.UpstreamResponse;

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

        UpstreamResponse resp = middleware.observe(ctx,
                c -> upstream.complete("Reply with exactly: ok", LiabilityExplanationService.MODEL));

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
}
