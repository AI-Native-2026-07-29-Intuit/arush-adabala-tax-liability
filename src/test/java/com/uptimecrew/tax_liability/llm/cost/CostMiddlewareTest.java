package com.uptimecrew.tax_liability.llm.cost;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import jakarta.servlet.http.HttpServletResponse;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.mock.web.MockHttpServletResponse;

/**
 * The cost arithmetic, the EMF cost line and the {@code X-Cost-Usd} header (W6 D4 Task 2).
 *
 * <p>Every expected figure here is hand-computed in the test rather than read back from the code
 * under test - a test that recomputes the cost the same way the implementation does would pass
 * against any consistent arithmetic, including consistently wrong arithmetic.
 *
 * <p>No Spring context, no servlet container and no provider call: {@link CostMiddleware} and
 * {@link CostLogger} are plain objects built with {@code new} precisely so this suite runs in
 * milliseconds and can assert on exact values.
 */
class CostMiddlewareTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private RecordingLogger logger;
    private CostMiddleware middleware;
    private RecordingResponse response;

    @BeforeEach
    void setUp() {
        logger = new RecordingLogger();
        middleware = new CostMiddleware(logger);
        response = new RecordingResponse();
    }

    // ---------------------------------------------------------------- costing

    /**
     * Hand-computed, and the two token classes are priced SEPARATELY - which is the whole point
     * of the W6 D5 split. Haiku 4.5 is $0.001 per 1,000 input tokens and $0.005 per 1,000 output:
     *
     *   1000 input  * 0.001 / 1000 = $0.0010
     *    500 output * 0.005 / 1000 = $0.0025
     *                                -------
     *                                $0.0035  = 350 units of 1e-5 USD
     *
     * The old blended rate charged 450 for this same call. That gap is not a rounding difference,
     * it is the blend having been struck at a 50/50 input:output split that no real call matches.
     */
    @Test
    void computesCostFromTokensInIntegerMinorUnits() {
        UpstreamResponse resp = UpstreamResponse.of("claude-haiku-4-5", 1_000, 500, 42, true, "text");

        assertThat(middleware.costUsdE5(resp)).isEqualTo(350L);
        assertThat(CostLogger.toUsd(350L)).isEqualByComparingTo(new BigDecimal("0.00350"));
    }

    /**
     * Output tokens cost 5x input on Haiku, and pricing them as if they were interchangeable is
     * exactly the bug the blended rate hid. Same total token count, different split, different
     * cost - a blended rate returns the same number for both of these and cannot tell them apart.
     */
    @Test
    void pricesOutputTokensHigherThanInput() {
        UpstreamResponse mostlyInput = UpstreamResponse.of("claude-haiku-4-5", 900, 100, 1, true, "t");
        UpstreamResponse mostlyOutput = UpstreamResponse.of("claude-haiku-4-5", 100, 900, 1, true, "t");

        assertThat(middleware.costUsdE5(mostlyInput)).isEqualTo(140L);   // 0.0009 + 0.0005
        assertThat(middleware.costUsdE5(mostlyOutput)).isEqualTo(460L);  // 0.0001 + 0.0045
        assertThat(mostlyInput.totalTokens()).isEqualTo(mostlyOutput.totalTokens());
    }

    /**
     * The shape of a real explain-liability call, measured against the live API: 144 input, 32
     * output. Pinned because it is the number every downstream artefact quotes - the X-Cost-Usd
     * header, the k6 cost threshold, the cost log - and a silent change to it should break a test
     * rather than a dashboard.
     */
    @Test
    void pricesARealExplainLiabilityCall() {
        UpstreamResponse resp = UpstreamResponse.of("claude-haiku-4-5", 144, 32, 1, true, "text");

        // 144*0.001/1000 + 32*0.005/1000 = 0.000144 + 0.000160 = $0.000304 -> 30e-5 (HALF_UP)
        assertThat(middleware.costUsdE5(resp)).isEqualTo(30L);
    }

    /**
     * The price gap between the two models is the argument for explain-liability using Haiku, so
     * it is asserted rather than assumed: identical token counts, Sonnet 5 costs twice as much.
     * (It was 3x against the superseded claude-sonnet-4-5; Sonnet 5 is both newer and cheaper,
     * which narrows the gap without changing which model this feature should use.)
     *
     * Input-only rows - the second column is input tokens, output is 0 - so these assert the
     * input rate in isolation.
     */
    @ParameterizedTest(name = "{0} x {1} input tokens costs {2}e-5 USD")
    @CsvSource({
        "claude-haiku-4-5, 1000, 100",
        "claude-sonnet-5,  1000, 200",
        "claude-haiku-4-5,    0,   0",
        "claude-haiku-4-5,    1,   0",
    })
    void pricesEachModelFromThePriceBook(String modelId, long tokens, long expectedE5) {
        UpstreamResponse resp = UpstreamResponse.of(modelId, tokens, 0, 1, true, "t");
        assertThat(middleware.costUsdE5(resp)).isEqualTo(expectedE5);
    }

    /**
     * A single input token of Haiku costs $0.000001, well below the 1e-5 unit, and rounds to 0.
     * That is a real and acceptable quantisation - but it must ROUND, not truncate into a wrong
     * bucket, so the boundary either side of half a unit is pinned. 5 input tokens = $0.000005,
     * exactly half a unit, which HALF_UP takes to 1.
     */
    @Test
    void roundsHalfUpAtTheMinorUnitBoundary() {
        assertThat(middleware.costUsdE5(haiku(1))).isZero();
        assertThat(middleware.costUsdE5(haiku(4))).isZero();        // 0.000004 -> 0.4 units
        assertThat(middleware.costUsdE5(haiku(5))).isEqualTo(1L);   // 0.000005 -> exactly 0.5, up
        // 0.001 * 1667 / 1000 = 0.001667 -> 166.7e-5 -> 167
        assertThat(middleware.costUsdE5(haiku(1_667))).isEqualTo(167L);
    }

    /**
     * An unpriced model throws instead of costing zero. This is the single most important
     * negative case in the suite: a silent zero produces a cost log and a header that look
     * completely healthy while reporting that a paid call was free, and nothing downstream can
     * tell the difference between "this model is free" and "this model is unknown".
     */
    @Test
    void unknownModelThrowsRatherThanCostingZero() {
        UpstreamResponse resp = UpstreamResponse.of("claude-haiku-4-5-v1:0", 1_000, 0, 1, true, "t");

        assertThatThrownBy(() -> middleware.costUsdE5(resp))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("no price for model")
                // The message must name the fix, because the likeliest cause of this failure is
                // somebody pasting a Bedrock-decorated model id.
                .hasMessageContaining("bare");
    }

    /**
     * Pricing keys off the REQUESTED alias, never the snapshot the provider reports serving.
     *
     * <p>Measured against the live Anthropic API: a request for {@code claude-haiku-4-5} returns
     * {@code "model": "claude-haiku-4-5-20251001"}. Pricing off the response id would throw on
     * the very first real call - and no stub-based test would ever catch it, because a stub
     * echoes back whatever model id it was handed. This case encodes the real observation so the
     * distinction cannot be refactored away by someone who has only seen the stubbed path.
     */
    @Test
    void pricesTheRequestedAliasNotTheResolvedSnapshot() {
        UpstreamResponse resp = new UpstreamResponse(
                "claude-haiku-4-5", "claude-haiku-4-5-20251001", 1_000, 0, 1, true, "t");

        // 1000 input, 0 output: 1000 * 0.001 / 1000 = $0.001 -> 100e-5
        assertThat(middleware.costUsdE5(resp)).isEqualTo(100L);
        assertThat(resp.servedByDifferentSnapshot()).isTrue();
    }

    /**
     * The snapshot reaches the cost line, so a month-old cost record can still be reconciled
     * against an invoice after the alias has floated to a new, differently-priced snapshot.
     */
    @Test
    void costLineCarriesBothAliasAndSnapshot() throws Exception {
        CallContext ctx = CallContext.detached(Instant.now(), "taxcalc", "acme", "explain-liability");

        middleware.observe(ctx, c -> new UpstreamResponse(
                "claude-haiku-4-5", "claude-haiku-4-5-20251001", 1_000, 0, 5, true, "t"));

        JsonNode line = MAPPER.readTree(logger.lines.get(0));
        assertThat(line.get("modelId").asText()).isEqualTo("claude-haiku-4-5");
        assertThat(line.get("resolvedModelId").asText()).isEqualTo("claude-haiku-4-5-20251001");
    }

    /** A provider reporting no model id degrades the audit trail; it does not fail the call. */
    @Test
    void missingResolvedModelIdFallsBackToTheRequestedId() {
        UpstreamResponse resp = new UpstreamResponse("claude-haiku-4-5", null, 10, 0, 1, true, "t");

        assertThat(resp.resolvedModelId()).isEqualTo("claude-haiku-4-5");
        assertThat(resp.servedByDifferentSnapshot()).isFalse();
    }

    // ---------------------------------------------------------- header + log

    @Test
    void attachesXCostUsdHeaderAndEmitsOneCostLine() {
        CallContext ctx = new CallContext(
                Instant.ofEpochMilli(1_700_000_000_000L), "taxcalc", "acme", "explain-liability", response);

        UpstreamResponse out = middleware.observe(ctx, c -> haiku(1_500));

        assertThat(out.totalTokens()).isEqualTo(1_500);
        // 1500 input tokens, 0 output: 1500 * 0.001 / 1000 = $0.0015
        assertThat(response.headers).containsEntry("X-Cost-Usd", "0.00150");
        assertThat(logger.lines).hasSize(1);
    }

    /**
     * The EMF document is parsed and asserted structurally, not regex-matched. The dimension set,
     * the namespace and the presence of all three dimension VALUES as top-level members are what
     * CloudWatch actually keys on - get any of them wrong and CloudWatch stores the log line and
     * silently discards the metric, which reads downstream as "nothing spent money".
     */
    @Test
    void emitsWellFormedEmbeddedMetricFormat() throws Exception {
        CallContext ctx = new CallContext(
                Instant.ofEpochMilli(1_700_000_000_000L), "taxcalc", "acme", "explain-liability", response);

        middleware.observe(ctx, c -> haiku(1_500));
        JsonNode line = MAPPER.readTree(logger.lines.get(0));

        JsonNode aws = line.get("_aws");
        assertThat(aws.get("Timestamp").asLong()).isEqualTo(1_700_000_000_000L);
        JsonNode directive = aws.get("CloudWatchMetrics").get(0);
        assertThat(directive.get("Namespace").asText()).isEqualTo("uptimecrew/llmproxy");
        assertThat(directive.get("Dimensions").get(0)).hasSize(3);
        assertThat(directive.get("Dimensions").get(0).toString()).isEqualTo("[\"service\",\"tenant\",\"feature\"]");

        // Every declared dimension must also exist as a top-level member.
        for (JsonNode dim : directive.get("Dimensions").get(0)) {
            assertThat(line.has(dim.asText()))
                    .as("dimension %s must be a top-level member or CloudWatch drops the metric", dim.asText())
                    .isTrue();
        }

        assertThat(line.get("service").asText()).isEqualTo("taxcalc");
        assertThat(line.get("tenant").asText()).isEqualTo("acme");
        assertThat(line.get("feature").asText()).isEqualTo("explain-liability");
        assertThat(line.get("modelId").asText()).isEqualTo("claude-haiku-4-5");
        assertThat(line.get("CostUsdE5").asLong()).isEqualTo(150L);
        assertThat(line.get("CostUsd").asDouble()).isEqualTo(0.0015);
        assertThat(line.get("LatencyMs").asLong()).isEqualTo(42L);
        assertThat(line.get("success").asBoolean()).isTrue();
    }

    /**
     * The reference implementation of this cost line concatenates values straight into a JSON
     * literal. {@code tenant} is caller-influenced, so a quote in it produces a malformed line -
     * and CloudWatch answers malformed EMF by dropping the metric and keeping the text, meaning
     * the cost series loses those calls and reads low with nothing failing anywhere.
     */
    @Test
    void tenantContainingJsonMetacharactersStillProducesParseableOutput() throws Exception {
        String hostileTenant = "ac\"me\\, inc";
        CallContext ctx = new CallContext(Instant.now(), "taxcalc", hostileTenant, "explain-liability", response);

        middleware.observe(ctx, c -> haiku(1_000));

        JsonNode line = MAPPER.readTree(logger.lines.get(0));
        assertThat(line.get("tenant").asText()).isEqualTo(hostileTenant);
    }

    /**
     * Ordering: the cost line is written before the header. A header write on an already-committed
     * response is dropped by the container, and if that happened first the call would vanish from
     * the cost series entirely - the spend would be real and unrecorded.
     */
    @Test
    void logsCostBeforeAttachingHeader() {
        List<String> order = new ArrayList<>();
        CostLogger orderingLogger = new CostLogger() {
            @Override
            public void emit(CallContext ctx, UpstreamResponse resp, long costUsdE5) {
                order.add("log");
            }
        };
        RecordingResponse resp = new RecordingResponse() {
            @Override
            public void setHeader(String name, String value) {
                order.add("header");
                super.setHeader(name, value);
            }
        };
        CallContext ctx = new CallContext(Instant.now(), "taxcalc", "acme", "explain-liability", resp);

        new CostMiddleware(orderingLogger).observe(ctx, c -> haiku(1_000));

        assertThat(order).containsExactly("log", "header");
    }

    /**
     * A call made outside an HTTP request is still costed and still logged; it simply has nowhere
     * to put a header. Making that an error would mean the cost path could only be used from a
     * controller, and a scheduled job that spends money would then have to bypass it.
     */
    @Test
    void detachedCallIsStillCostedAndLogged() {
        CallContext ctx = CallContext.detached(Instant.now(), "taxcalc", "shared", "warmup");

        middleware.observe(ctx, c -> haiku(1_000));

        assertThat(logger.lines).hasSize(1);
        // 1000 input tokens, 0 output: $0.001 -> 100e-5
        assertThat(logger.lines.get(0)).contains("\"CostUsdE5\":100");
    }

    // ------------------------------------------------------------- contracts

    @Test
    void rejectsBlankAttributionKeys() {
        // A blank dimension value is worse than a missing one: it is a real CloudWatch dimension
        // whose value is "", which silently splits the cost series into two.
        assertThrows(IllegalArgumentException.class,
                () -> new CallContext(Instant.now(), "taxcalc", "", "explain-liability", null));
        assertThrows(NullPointerException.class,
                () -> new CallContext(Instant.now(), "taxcalc", null, "explain-liability", null));
    }

    @Test
    void rejectsNegativeTokenCounts() {
        assertThrows(IllegalArgumentException.class,
                () -> UpstreamResponse.of("claude-haiku-4-5", -1, 0, 0, true, "t"));
    }

    @Test
    void rejectsNullUpstreamResponse() {
        CallContext ctx = CallContext.detached(Instant.now(), "taxcalc", "shared", "explain-liability");
        assertThatThrownBy(() -> middleware.observe(ctx, c -> null))
                .isInstanceOf(NullPointerException.class)
                .hasMessageContaining("null response");
    }

    /** Input-only Haiku response - the argument is INPUT tokens, output is 0. */
    private static UpstreamResponse haiku(long inputTokens) {
        return UpstreamResponse.of("claude-haiku-4-5", inputTokens, 0, 42, true, "explanation text");
    }

    /** Captures rendered cost lines instead of writing them to a log appender. */
    private static final class RecordingLogger extends CostLogger {
        private final List<String> lines = new ArrayList<>();

        @Override
        public void emit(CallContext ctx, UpstreamResponse resp, long costUsdE5) {
            lines.add(render(ctx, resp, costUsdE5));
        }
    }

    /**
     * Spring's {@link MockHttpServletResponse} with a header map exposed under the name the
     * assertions read. Extending the real Spring mock rather than hand-rolling an
     * {@link HttpServletResponse} matters here: {@code setHeader} has container semantics
     * (replace-not-append, case-insensitive names) that a hand-written stub would quietly get
     * wrong, and this test's whole subject is that the header carries an exact string.
     */
    private static class RecordingResponse extends MockHttpServletResponse {
        private final Map<String, String> headers = new LinkedHashMap<>();

        @Override
        public void setHeader(String name, String value) {
            headers.put(name, value);
            super.setHeader(name, value);
        }
    }
}
