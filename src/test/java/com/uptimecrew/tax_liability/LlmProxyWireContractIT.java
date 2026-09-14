package com.uptimecrew.tax_liability;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.wireMockConfig;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.tomakehurst.wiremock.junit5.WireMockExtension;
import com.github.tomakehurst.wiremock.verification.LoggedRequest;
import com.uptimecrew.tax_liability.llm.LiabilityExplanationService;
import com.uptimecrew.tax_liability.llm.cost.CostLogger;
import com.uptimecrew.tax_liability.llm.cost.CostResponseHeader;
import com.uptimecrew.tax_liability.security.ScopeAndRoleAuthoritiesConverter;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;
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
 * {@code POST /v1/completions} end to end against Anthropic's real HTTP wire format (W6 D4 Task 2).
 *
 * <p><b>This closes the seam the other two ITs leave open.</b> {@code LlmProxyCompletionsIT} stubs
 * the {@code ChatModel} <em>bean</em>, so Spring AI's request serialization, HTTP client and
 * response parsing never run - it proves routing and accounting, and nothing about the wire.
 * {@code LlmProxyLiveCostIT} runs the true paid path but is refused by the Anthropic workspace
 * spend cap (see that class). This one stubs only the <b>far side of the socket</b>: everything
 * from the JWT to the outbound TCP connection is production code, and what comes back is a
 * byte-accurate Anthropic Messages response carrying a real {@code usage} block.
 *
 * <p>So the residual gap is narrowed to exactly one proposition - "Anthropic's servers accept this
 * request" - which no test can settle while the workspace is capped. Everything downstream of the
 * response bytes, which is all of the cost machinery, is verified against the genuine format.
 *
 * <p><b>The outbound assertions are the point.</b> Asserting on the request WireMock <em>received</em>
 * is what proves the deliverable's model-id rule on the wire rather than in a constant: the JSON
 * that leaves this process must carry the bare {@code claude-haiku-4-5}, with no vendor prefix and
 * no gateway version suffix. A test that only checks {@code LiabilityExplanationService.MODEL}
 * would pass even if something downstream rewrote the id before sending it.
 */
@Testcontainers
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class LlmProxyWireContractIT {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /** The usage block the stubbed response reports; see {@link #costHeaderIsDerivedFromRealUsageBlock}. */
    private static final int INPUT_TOKENS = 14;
    private static final int OUTPUT_TOKENS = 5;

    /** The dated snapshot a real API resolves the floating alias to. */
    private static final String SNAPSHOT = "claude-haiku-4-5-20251001";

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> PG = new PostgreSQLContainer<>(TestImages.POSTGRES);

    @Container
    @ServiceConnection
    static final MongoDBContainer MONGO = new MongoDBContainer("mongo:7");

    @Container
    @ServiceConnection(name = "redis")
    static final GenericContainer<?> REDIS = new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    /** Stands in for {@code https://api.anthropic.com}; the {@code test} profile points at :8092. */
    @RegisterExtension
    static final WireMockExtension WM = WireMockExtension.newInstance()
            .options(wireMockConfig().port(8092))
            .build();

    @Autowired
    private MockMvc mvc;

    private Logger costLogbackLogger;
    private ListAppender<ILoggingEvent> appender;

    @BeforeEach
    void attachAppenderAndStubAnthropic() {
        costLogbackLogger = (Logger) LoggerFactory.getLogger(CostLogger.class);
        appender = new ListAppender<>();
        appender.start();
        costLogbackLogger.addAppender(appender);

        // A real Anthropic Messages response, including the usage block the cost is derived from
        // and the dated snapshot in `model`. Copied in the provider's own shape rather than a
        // convenient subset: the point of this IT is that the genuine format parses.
        WM.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlEqualTo("/v1/messages"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "id": "msg_01XyZaBcDeFgHiJkLmNoPq",
                                  "type": "message",
                                  "role": "assistant",
                                  "model": "%s",
                                  "content": [{"type": "text", "text": "ok"}],
                                  "stop_reason": "end_turn",
                                  "stop_sequence": null,
                                  "usage": {"input_tokens": %d, "output_tokens": %d}
                                }
                                """.formatted(SNAPSHOT, INPUT_TOKENS, OUTPUT_TOKENS))));
    }

    @AfterEach
    void detachAppender() {
        costLogbackLogger.detachAppender(appender);
    }

    /**
     * The Done-when line, with the cost derived from a genuine {@code usage} block that travelled
     * over a real HTTP connection.
     *
     * <p>Hand-computed, priced per token class: 14 input at $0.001/1K and 5 output at
     * $0.005/1K costs (14*0.001 + 5*0.005) / 1000 = $0.000039, which rounds HALF_UP at
     * scale 5 to {@code 0.00004}.
     */
    @Test
    void costHeaderIsDerivedFromRealUsageBlock() throws Exception {
        MvcResult result = mvc.perform(post("/v1/completions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"prompt":"Reply with exactly: ok","feature":"explain-liability"}
                                """)
                        .with(readerJwtForTenant("wire-contract-user", "tally")))
                .andExpect(status().isOk())
                .andReturn();

        assertThat(result.getResponse().getHeader(CostResponseHeader.HEADER)).isEqualTo("0.00004");

        JsonNode body = MAPPER.readTree(result.getResponse().getContentAsString());
        assertThat(body.get("inputTokens").asLong()).isEqualTo(INPUT_TOKENS);
        assertThat(body.get("outputTokens").asLong()).isEqualTo(OUTPUT_TOKENS);
        assertThat(body.get("model").asText()).isEqualTo(LiabilityExplanationService.MODEL);
        assertThat(body.get("resolvedModel").asText()).isEqualTo(SNAPSHOT);
        assertThat(body.get("text").asText()).isEqualTo("ok");

        List<String> costLines = appender.list.stream()
                .map(ILoggingEvent::getFormattedMessage)
                .filter(line -> line.startsWith("{"))
                .toList();
        assertThat(costLines).hasSize(1);

        JsonNode line = MAPPER.readTree(costLines.get(0));
        assertThat(line.at("/_aws/CloudWatchMetrics/0/Namespace").asText()).isEqualTo("uptimecrew/llmproxy");
        assertThat(line.get("tenant").asText()).isEqualTo("tally");
        assertThat(line.get("feature").asText()).isEqualTo("explain-liability");
        assertThat(line.get("CostUsdE5").asLong()).isEqualTo(4L);
        assertThat(line.get("inputTokens").asLong()).isEqualTo(INPUT_TOKENS);
        assertThat(line.get("outputTokens").asLong()).isEqualTo(OUTPUT_TOKENS);
    }

    /**
     * The model id that actually leaves the process is the bare alias.
     *
     * <p>This is the deliverable's {@code grep} gate expressed as behaviour instead of as text: the
     * gate proves the forbidden spellings are absent from the source, this proves the correct one
     * is what goes on the wire.
     */
    @Test
    void sendsBareModelIdOnTheWire() throws Exception {
        mvc.perform(post("/v1/completions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"prompt":"Reply with exactly: ok","feature":"explain-liability"}
                                """)
                        .with(readerJwtForTenant("wire-model-user", "tally")))
                .andExpect(status().isOk());

        List<LoggedRequest> sent = WM.findAll(postRequestedFor(urlEqualTo("/v1/messages")));
        assertThat(sent).hasSize(1);

        JsonNode outbound = MAPPER.readTree(sent.get(0).getBodyAsString());
        assertThat(outbound.get("model").asText()).isEqualTo("claude-haiku-4-5");
        assertThat(outbound.get("model").asText()).doesNotContain("anthropic.");
        assertThat(outbound.get("model").asText()).doesNotEndWith("-v1:0");

        // The key reached the wire through ${ANTHROPIC_API_KEY:dummy} - the same placeholder the
        // K8s Secret populates in production. Its value is environment-dependent (the `dummy`
        // fallback locally), so this asserts the header was populated, never what it contains.
        assertThat(sent.get(0).getHeader("x-api-key")).isNotBlank();
        assertThat(sent.get(0).getHeader("anthropic-version")).isNotBlank();
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
