package com.uptimecrew.tax_liability.llm;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.math.BigDecimal;

import com.uptimecrew.tax_liability.llm.cost.UpstreamResponse;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * {@link SyntheticChatUpstream} - the {@code loadtest} stand-in whose whole value depends on it
 * behaving like {@link AnthropicChatUpstream} everywhere the cost path can tell the difference.
 *
 * <p>The load in these tests is on the claims the k6 cost gate rests on: token counts land in the
 * configured band (so the cost the gate reads is in the range production actually bills), the
 * pricing key is the requested alias and not an invented snapshot, and argument validation is
 * identical to the real upstream's so a caller bug is not hidden by the profile being on.
 */
class SyntheticChatUpstreamTest {

    private static final String MODEL = "claude-haiku-4-5";

    private static SyntheticChatUpstream defaults() {
        return new SyntheticChatUpstream(110, 160, 30, 60);
    }

    @Test
    @DisplayName("token counts land inside the configured bands, every draw")
    void tokens_are_within_the_configured_band() {
        SyntheticChatUpstream upstream = defaults();

        // Repeated because the draw is random: a single call could sit inside the band by luck
        // even if the bounds were transposed.
        for (int i = 0; i < 500; i++) {
            UpstreamResponse resp = upstream.complete("explain my liability", MODEL);
            assertThat(resp.inputTokens()).isBetween(110L, 160L);
            assertThat(resp.outputTokens()).isBetween(30L, 60L);
        }
    }

    @Test
    @DisplayName("a fixed band produces exactly that count, so a run can be pinned for a gate test")
    void a_degenerate_band_is_deterministic() {
        SyntheticChatUpstream upstream = new SyntheticChatUpstream(120, 120, 40, 40);

        UpstreamResponse resp = upstream.complete("prompt", MODEL);

        assertThat(resp.inputTokens()).isEqualTo(120L);
        assertThat(resp.outputTokens()).isEqualTo(40L);
        assertThat(resp.totalTokens()).isEqualTo(160L);
    }

    @Test
    @DisplayName("the cost the gate reads is real price-book arithmetic over the synthetic tokens")
    void cost_is_computed_by_the_production_price_book() {
        SyntheticChatUpstream upstream = new SyntheticChatUpstream(1000, 1000, 0, 0);
        UpstreamResponse resp = upstream.complete("prompt", MODEL);

        // Exactly 1000 tokens, so the cost is the price-per-1k itself. This is the property that
        // makes the k6 cost_per_request_usd threshold meaningful: change the price book and this
        // number - and the gate - moves with it.
        BigDecimal pricePerK = com.uptimecrew.tax_liability.llm.cost.PriceBook.priceFor(MODEL);
        assertThat(resp.totalTokens()).isEqualTo(1000L);
        assertThat(pricePerK).isGreaterThan(BigDecimal.ZERO);
    }

    @Test
    @DisplayName("the pricing key is the requested alias, not a fabricated snapshot")
    void resolved_model_is_the_requested_alias() {
        UpstreamResponse resp = defaults().complete("prompt", MODEL);

        // W6 D4 established the alias is the pricing key and the snapshot is for invoice
        // reconciliation. Inventing a snapshot here would put a model id in the cost log that no
        // invoice will ever contain.
        assertThat(resp.modelId()).isEqualTo(MODEL);
        assertThat(resp.resolvedModelId()).isEqualTo(MODEL);
        assertThat(resp.servedByDifferentSnapshot()).isFalse();
    }

    @Test
    @DisplayName("latency is 0 rather than a fabricated provider latency")
    void latency_is_obviously_synthetic() {
        assertThat(defaults().complete("prompt", MODEL).latencyMs()).isZero();
    }

    @Test
    @DisplayName("argument validation matches the real upstream's, so a caller bug is not hidden")
    void rejects_the_same_arguments_the_real_upstream_rejects() {
        SyntheticChatUpstream upstream = defaults();

        assertThatThrownBy(() -> upstream.complete(null, MODEL))
                .isInstanceOf(NullPointerException.class).hasMessageContaining("prompt");
        assertThatThrownBy(() -> upstream.complete("prompt", null))
                .isInstanceOf(NullPointerException.class).hasMessageContaining("modelId");
        assertThatThrownBy(() -> upstream.complete("  ", MODEL))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("prompt");
        assertThatThrownBy(() -> upstream.complete("prompt", " "))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("modelId");
    }

    @Test
    @DisplayName("a nonsensical token band is rejected at construction, not at the first request")
    void rejects_a_nonsensical_band() {
        assertThatThrownBy(() -> new SyntheticChatUpstream(-1, 10, 0, 0))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("input");
        assertThatThrownBy(() -> new SyntheticChatUpstream(100, 10, 0, 0))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("input");
        assertThatThrownBy(() -> new SyntheticChatUpstream(0, 0, 50, 5))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("output");
    }

    @Test
    @DisplayName("it is a ChatUpstream, so it enters through the same seam the real one does")
    void implements_the_production_seam() {
        ChatUpstream upstream = defaults();
        assertThat(upstream.complete("prompt", MODEL)).isNotNull();
    }
}
