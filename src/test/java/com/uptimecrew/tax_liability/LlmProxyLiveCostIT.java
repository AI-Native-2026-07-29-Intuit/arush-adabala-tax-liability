package com.uptimecrew.tax_liability;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.math.BigDecimal;
import java.util.List;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.llm.LiabilityExplanationService;
import com.uptimecrew.tax_liability.llm.cost.CostLogger;
import com.uptimecrew.tax_liability.llm.cost.CostResponseHeader;
import com.uptimecrew.tax_liability.security.ScopeAndRoleAuthoritiesConverter;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * The W6 D4 Task 2 Done-when line executed for real: {@code POST /v1/completions} against the
 * live Anthropic API, paying for real tokens (W6 D4 Task 2).
 *
 * <p><b>What this closes that no other test does.</b> {@code LlmProxyCompletionsIT} proves the
 * route, the filter chain and the accounting with a stubbed model; {@code AnthropicCostPathLiveIT}
 * proves a real paid call but constructs the upstream directly and never touches HTTP. Each covers
 * one half of the seam. This one runs the whole path end to end - real JWT, real filter chain, real
 * {@code AnthropicChatUpstream}, real API, real {@code usage} token counts, real cost header - so
 * the claim "a 200 from this endpoint carries a non-zero {@code X-Cost-Usd} derived from the
 * provider's own token counts" is tested rather than inferred from two adjacent tests.
 *
 * <p><b>Two properties are overridden, and only two.</b> The {@code test} profile pins
 * {@code spring.ai.anthropic.base-url} to a WireMock address for
 * {@code LlmSummaryServiceAnthropicContractIT}, so it is pointed back at the real API here.
 * {@code max-tokens} is cut to a handful to keep the deliberate spend negligible. The API key is
 * pointedly NOT overridden: it resolves from the {@code ANTHROPIC_API_KEY} environment variable
 * through the main profile's {@code ${ANTHROPIC_API_KEY:dummy}} placeholder, which is the same
 * resolution path the K8s Secret uses in production - so this test exercises that wiring too.
 *
 * <p><b>Gated on that variable being present</b>, so a CI run without a key skips rather than
 * fails. It costs a fraction of a cent per run:
 *
 * <pre>{@code
 * ANTHROPIC_API_KEY=sk-ant-... ./gradlew test --tests '*LlmProxyLiveCostIT'
 * }</pre>
 */
@Testcontainers
@SpringBootTest(properties = {
    "spring.ai.anthropic.base-url=https://api.anthropic.com",
    "spring.ai.anthropic.chat.options.max-tokens=16"
})
@AutoConfigureMockMvc
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named = "ANTHROPIC_API_KEY", matches = ".+",
        disabledReason = "no ANTHROPIC_API_KEY in the environment - this test calls the real, paid API")
class LlmProxyLiveCostIT {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> PG = new PostgreSQLContainer<>(TestImages.POSTGRES);

    @Container
    @ServiceConnection
    static final MongoDBContainer MONGO = new MongoDBContainer("mongo:7");

    @Container
    @ServiceConnection(name = "redis")
    static final GenericContainer<?> REDIS = new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    @Autowired
    private MockMvc mvc;

    private Logger costLogbackLogger;
    private ListAppender<ILoggingEvent> appender;

    @BeforeEach
    void attachAppender() {
        costLogbackLogger = (Logger) LoggerFactory.getLogger(CostLogger.class);
        appender = new ListAppender<>();
        appender.start();
        costLogbackLogger.addAppender(appender);
    }

    @AfterEach
    void detachAppender() {
        costLogbackLogger.detachAppender(appender);
    }

    @Test
    void realCallReturns200WithNonZeroCostHeaderAndOneEmfLine() throws Exception {
        MvcResult result;
        try {
            result = mvc.perform(post("/v1/completions")
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("""
                                    {"prompt":"Reply with exactly: ok","feature":"explain-liability"}
                                    """)
                            .with(readerJwtForTenant("live-cost-user", "tally")))
                    .andExpect(status().isOk())
                    .andReturn();
        } catch (Exception ex) {
            abortIfWorkspaceSpendCapped(ex);
            throw ex;
        }

        // 1. A non-zero cost header, parsed as the k6 threshold parses it - plain decimal, never
        //    scientific notation, which is the failure mode Double.toString would reintroduce.
        String header = result.getResponse().getHeader(CostResponseHeader.HEADER);
        assertThat(header).isNotNull();
        assertThat(header).doesNotContain("E").doesNotContain("e");
        BigDecimal costUsd = new BigDecimal(header);
        assertThat(costUsd).isGreaterThan(BigDecimal.ZERO);
        assertThat(costUsd.scale()).isEqualTo(5);

        // 2. Real usage tokens came back on both sides. A provider returning no usage block would
        //    default both to 0 and produce a free-looking paid call - the exact failure this
        //    feature exists to prevent, and the one a stubbed model can never catch.
        JsonNode body = MAPPER.readTree(result.getResponse().getContentAsString());
        assertThat(body.get("inputTokens").asLong()).isPositive();
        assertThat(body.get("outputTokens").asLong()).isPositive();
        assertThat(body.get("model").asText()).isEqualTo(LiabilityExplanationService.MODEL);

        // 3. The bare alias was accepted by the real API and resolved to a dated snapshot. This is
        //    the assertion that proves the model id needs no 'anthropic.' prefix and no '-v1:0'
        //    suffix: if it did, the call above would have failed before reaching here.
        assertThat(body.get("resolvedModel").asText()).startsWith(LiabilityExplanationService.MODEL);

        // 4. Exactly one EMF cost line, in the right namespace, carrying the attribution keys.
        List<String> costLines = appender.list.stream()
                .map(ILoggingEvent::getFormattedMessage)
                .filter(line -> line.startsWith("{"))
                .toList();
        assertThat(costLines).hasSize(1);

        JsonNode line = MAPPER.readTree(costLines.get(0));
        assertThat(line.at("/_aws/CloudWatchMetrics/0/Namespace").asText()).isEqualTo("uptimecrew/llmproxy");
        assertThat(line.at("/_aws/CloudWatchMetrics/0/Dimensions/0").toString())
                .isEqualTo("[\"service\",\"tenant\",\"feature\"]");
        assertThat(line.get("tenant").asText()).isEqualTo("tally");
        assertThat(line.get("feature").asText()).isEqualTo("explain-liability");
        assertThat(line.get("CostUsd").asDouble()).isPositive();

        // 5. The header and the log line are two renderings of one number, not two computations.
        assertThat(line.get("CostUsdE5").asLong())
                .isEqualTo(costUsd.movePointRight(5).longValueExact());

        System.out.printf("LIVE model=%s resolved=%s in=%d out=%d X-Cost-Usd=%s%n",
                body.get("model").asText(), body.get("resolvedModel").asText(),
                body.get("inputTokens").asLong(), body.get("outputTokens").asLong(), header);
    }

    /**
     * Skip rather than fail when the Anthropic workspace spend limit has already refused the call.
     *
     * <p><b>This is not papering over a failure - it is the distinction the suite exists to make.</b>
     * A capped workspace and a broken cost path produce the same red test but demand opposite
     * responses: one is the Task 2 guardrail working exactly as designed (the spend limit is the
     * hard cap, enforced by the party doing the billing), the other is a defect. Reporting the cap
     * as a skip, with the provider's own message as the reason, keeps the failure channel meaning
     * "the code is wrong".
     *
     * <p>Note what still gets proven even on this path: the request authenticated, reached the real
     * API and came back with a {@code request_id}. That exercises the whole
     * {@code ANTHROPIC_API_KEY} → {@code ${ANTHROPIC_API_KEY:dummy}} → Spring AI resolution chain
     * the K8s Secret uses in production. Only the token accounting goes unverified.
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

    private static RequestPostProcessor readerJwtForTenant(String subject, String tenant) {
        return jwt()
                .jwt(j -> j.subject(subject)
                        .claim("scope", "taxpayers.read")
                        .claim("roles", List.of("TAXPAYER_READER"))
                        .claim("tenant", tenant))
                .authorities(new ScopeAndRoleAuthoritiesConverter());
    }
}
