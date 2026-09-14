package com.uptimecrew.tax_liability;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.graphql.TaxpayerSummary;
import com.uptimecrew.tax_liability.llm.StubChatClientFactory;
import com.uptimecrew.tax_liability.llm.cost.CostLogger;
import com.uptimecrew.tax_liability.llm.cost.CostResponseHeader;
import com.uptimecrew.tax_liability.security.ScopeAndRoleAuthoritiesConverter;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
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
 * The W6 D4 Task 2 Done-when case for {@code POST /v1/completions}, over real HTTP.
 *
 * <p><b>Why this exists on top of {@code LlmProxyControllerTest}.</b> That test calls the
 * controller as a plain object, which proves the accounting arithmetic and nothing about whether
 * the route is reachable. Three things sit between a correct controller and a 200 here, and all
 * three are configuration rather than code: the handler mapping has to register {@code /v1}, the
 * {@link com.uptimecrew.tax_liability.security.SecurityConfig} chain ends in {@code denyAll} and
 * so must name the prefix explicitly, and {@code @PreAuthorize} has to admit the caller's
 * authorities. Each would leave the unit test green and return 401/403/404 to a real caller.
 *
 * <p>The upstream is stubbed with {@link StubChatClientFactory} - the Anthropic-free
 * {@code ChatModel} the other ITs use - so this exercises the real
 * {@link com.uptimecrew.tax_liability.llm.AnthropicChatUpstream}, the real
 * {@link com.uptimecrew.tax_liability.llm.cost.CostMiddleware} and the real filter chain without
 * spending money. The paid path is covered separately, and only on demand, by
 * {@code AnthropicCostPathLiveIT}.
 */
@Testcontainers
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@Import(LlmProxyCompletionsIT.StubChatModelConfig.class)
class LlmProxyCompletionsIT {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /** The stub reports 17 prompt + 42 completion tokens; see {@link #assertsCostHeader}. */
    private static final int PROMPT_TOKENS = 17;
    private static final int COMPLETION_TOKENS = 42;

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

    /**
     * The Done-when line, end to end: tenant {@code tally}, feature {@code explain-liability},
     * 200, a non-zero {@code X-Cost-Usd}, and exactly one EMF cost line in namespace
     * {@code uptimecrew/llmproxy}.
     *
     * <p>Hand-computed, priced per token class: 17 input at $0.001/1K and 42 output at
     * $0.005/1K costs (17*0.001 + 42*0.005) / 1000 = $0.000227, which rounds HALF_UP at
     * scale 5 to {@code 0.00023}. Note the output tokens dominate despite being fewer -
     * the exact asymmetry a blended rate cannot express.
     */
    @Test
    void assertsCostHeader() throws Exception {
        MvcResult result = mvc.perform(post("/v1/completions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"prompt":"explain this liability","feature":"explain-liability"}
                                """)
                        .with(readerJwtForTenant("cost-user", "tally")))
                .andExpect(status().isOk())
                .andReturn();

        String header = result.getResponse().getHeader(CostResponseHeader.HEADER);
        assertThat(header).isEqualTo("0.00023");
        assertThat(Double.parseDouble(header)).isGreaterThan(0.0);

        JsonNode body = MAPPER.readTree(result.getResponse().getContentAsString());
        assertThat(body.get("model").asText()).isEqualTo("claude-haiku-4-5");
        assertThat(body.get("feature").asText()).isEqualTo("explain-liability");
        assertThat(body.get("inputTokens").asLong()).isEqualTo(PROMPT_TOKENS);
        assertThat(body.get("outputTokens").asLong()).isEqualTo(COMPLETION_TOKENS);

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
        assertThat(line.get("CostUsd").asDouble()).isEqualTo(0.00023);
    }

    /** The route is authenticated, not accidentally public - it spends money per call. */
    @Test
    void returns401WhenAnonymous() throws Exception {
        mvc.perform(post("/v1/completions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"prompt\":\"p\",\"feature\":\"explain-liability\"}"))
                .andExpect(status().isUnauthorized());
    }

    /** A model the price book cannot price is refused with a 400, not a 500. */
    @Test
    void returns400ForUnpriceableModel() throws Exception {
        mvc.perform(post("/v1/completions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"prompt":"p","model":"claude-not-a-real-model","feature":"explain-liability"}
                                """)
                        .with(readerJwtForTenant("bad-model-user", "tally")))
                .andExpect(status().isBadRequest());
    }

    private static RequestPostProcessor readerJwtForTenant(String subject, String tenant) {
        return jwt()
                .jwt(j -> j.subject(subject)
                        .claim("scope", "taxpayers.read")
                        .claim("roles", List.of("TAXPAYER_READER"))
                        .claim("tenant", tenant))
                .authorities(new ScopeAndRoleAuthoritiesConverter());
    }

    /**
     * Same reasoning as {@code TaxpayerGraphQlIT.StubChatModelConfig}: swap the app's real
     * Anthropic-backed builder for the deterministic stub so this IT exercises every layer except
     * the paid network call.
     */
    @TestConfiguration
    static class StubChatModelConfig {

        @Bean
        @Primary
        ChatClient.Builder chatClientBuilder() {
            return StubChatClientFactory.builderReturning(
                    new TaxpayerSummary("SINGLE", 950.0, 1, "LOW"), PROMPT_TOKENS, COMPLETION_TOKENS);
        }
    }
}
