package com.uptimecrew.tax_liability.llmproxy;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.llm.AnthropicChatUpstream;
import com.uptimecrew.tax_liability.llm.cost.CostLogger;
import com.uptimecrew.tax_liability.llm.cost.CostMiddleware;
import com.uptimecrew.tax_liability.llm.cost.CostResponseHeader;
import com.uptimecrew.tax_liability.llm.cost.UpstreamResponse;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.oauth2.jwt.Jwt;

/**
 * The {@code POST /v1/completions} proxy route (W6 D4 Task 2).
 *
 * <p>No Spring context and no servlet container: the controller is built with {@code new} so this
 * runs in milliseconds. The upstream is mocked - calling the real one costs money - but
 * {@link CostMiddleware} and {@link CostLogger} are the real objects, because the thing worth
 * asserting is that routing a call through this controller produces the same accounting a call
 * through any other path would.
 */
class LlmProxyControllerTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private AnthropicChatUpstream upstream;
    private LlmProxyController controller;
    private MockHttpServletResponse response;
    private Logger costLogbackLogger;
    private ListAppender<ILoggingEvent> appender;

    @BeforeEach
    void setUp() {
        upstream = mock(AnthropicChatUpstream.class);
        controller = new LlmProxyController(upstream, new CostMiddleware(new CostLogger()), "taxcalc");
        response = new MockHttpServletResponse();

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
     * The Done-when case: tenant {@code tally}, feature {@code explain-liability}, 200 with a
     * non-zero cost header and exactly one EMF line in the right namespace.
     *
     * <p>Hand-computed, priced per token class: Haiku 4.5 is $0.001 per 1,000 input tokens
     * and $0.005 per 1,000 output, so 1,000 input + 500 output costs
     * (1000*0.001 + 500*0.005) / 1000 = $0.0035 - {@code 0.00350} as the header renders it.
     */
    @Test
    void returnsCompletionWithNonZeroCostHeaderAndOneEmfLine() throws Exception {
        when(upstream.complete(anyString(), eq("claude-haiku-4-5"))).thenReturn(
                new UpstreamResponse("claude-haiku-4-5", "claude-haiku-4-5-20251001",
                        1_000, 500, 42, true, "Your liability is what it is."));

        ResponseEntity<CompletionResponse> result = controller.completions(
                new CompletionRequest("explain this", null, "explain-liability"),
                jwtWithTenant("tally"), response);

        assertThat(result.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(result.getBody().text()).isEqualTo("Your liability is what it is.");
        assertThat(result.getBody().resolvedModel()).isEqualTo("claude-haiku-4-5-20251001");
        assertThat(result.getBody().feature()).isEqualTo("explain-liability");

        String header = response.getHeader(CostResponseHeader.HEADER);
        assertThat(header).isEqualTo("0.00350");
        assertThat(Double.parseDouble(header)).isGreaterThan(0.0);

        assertThat(emittedLines()).hasSize(1);
        JsonNode line = MAPPER.readTree(emittedLines().get(0));
        assertThat(line.at("/_aws/CloudWatchMetrics/0/Namespace").asText())
                .isEqualTo("uptimecrew/llmproxy");
        assertThat(line.get("tenant").asText()).isEqualTo("tally");
        assertThat(line.get("feature").asText()).isEqualTo("explain-liability");
        assertThat(line.get("service").asText()).isEqualTo("taxcalc");
        assertThat(line.get("CostUsd").asDouble()).isEqualTo(0.0035);
    }

    /** A request that names no model is billed against the default, not against nothing. */
    @Test
    void defaultsToHaikuWhenModelOmitted() {
        assertThat(new CompletionRequest("p", null, "f").model()).isEqualTo("claude-haiku-4-5");
        assertThat(new CompletionRequest("p", "  ", "f").model()).isEqualTo("claude-haiku-4-5");
    }

    /**
     * An unpriceable model is refused BEFORE the upstream call. The assertion that matters is the
     * {@code never()}: a 400 that still bought the tokens is the failure this ordering prevents.
     */
    @Test
    void rejectsUnpriceableModelWithoutSpending() {
        assertThatThrownBy(() -> controller.completions(
                new CompletionRequest("explain this", "anthropic.claude-3-sonnet-v1:0", "explain-liability"),
                jwtWithTenant("tally"), response))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("no price for model");

        verify(upstream, never()).complete(anyString(), anyString());
        assertThat(response.getHeader(CostResponseHeader.HEADER)).isNull();
        assertThat(emittedLines()).isEmpty();
    }

    /** That exception becomes a 400, not a 500 - the server refused correctly, it did not break. */
    @Test
    void mapsIllegalArgumentToBadRequest() {
        ResponseEntity<Map<String, String>> result =
                controller.badRequest(new IllegalArgumentException("no price for model xyz"));

        assertThat(result.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(result.getBody().get("error")).isEqualTo("invalid_request");
    }

    /** A blank prompt or feature is rejected at the boundary, before any cost path is entered. */
    @Test
    void rejectsBlankPromptAndBlankFeature() {
        assertThatThrownBy(() -> new CompletionRequest("  ", null, "explain-liability"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("prompt");
        assertThatThrownBy(() -> new CompletionRequest("explain this", null, "  "))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("feature");
    }

    /**
     * A JWT with no {@code tenant} claim bills {@code shared} rather than failing the request or
     * - worse - emitting a blank dimension, which would silently split the CloudWatch series.
     */
    @Test
    void billsSharedWhenTenantClaimMissing() throws Exception {
        when(upstream.complete(anyString(), anyString())).thenReturn(
                UpstreamResponse.of("claude-haiku-4-5", 10, 4, 5, true, "ok"));

        controller.completions(new CompletionRequest("p", null, "explain-liability"),
                jwtWithoutTenant(), response);

        assertThat(MAPPER.readTree(emittedLines().get(0)).get("tenant").asText()).isEqualTo("shared");
    }

    private static Jwt jwtWithTenant(String tenant) {
        return Jwt.withTokenValue("token")
                .header("alg", "none")
                .subject("user-1")
                .claim("tenant", tenant)
                .build();
    }

    private static Jwt jwtWithoutTenant() {
        return Jwt.withTokenValue("token")
                .header("alg", "none")
                .subject("user-1")
                .claim("scope", "taxpayers.read")
                .build();
    }

    /**
     * The EMF lines the real {@link CostLogger} actually wrote, newest last.
     *
     * <p>Read off a Logback {@link ListAppender} rather than from a stubbed logger: the line that
     * matters is the one that reaches stdout, since stdout is what ships to CloudWatch Logs. A
     * stub would prove the renderer works and say nothing about whether anything emits it.
     */
    private List<String> emittedLines() {
        return appender.list.stream().map(ILoggingEvent::getFormattedMessage).toList();
    }
}
