package com.uptimecrew.tax_liability;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.post;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.wireMockConfig;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import com.github.tomakehurst.wiremock.junit5.WireMockExtension;
import com.uptimecrew.tax_liability.graphql.TaxpayerSummary;
import com.uptimecrew.tax_liability.llm.LlmSummaryService;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel.EmbeddedLiability;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.web.client.RestClientException;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * Proves the {@link LlmSummaryService} to Anthropic HTTP contract against a WireMock stub
 * standing in for {@code https://api.anthropic.com} (via {@code spring.ai.anthropic.base-url} in
 * the {@code test} profile), rather than mocking Spring AI's {@code ChatModel} bean the way {@link
 * TaxpayerGraphQlIT} does. Unlike that class, this one exercises the real {@code AnthropicApi}
 * HTTP client end to end - real request serialization, real {@code x-api-key}/
 * {@code anthropic-version} headers, real response parsing - so it also catches wiring mistakes a
 * Java-level mock can't see. The second test reproduces, deterministically, the exact failure mode
 * found by hand while smoke-testing {@code summarizeTaxpayer} with the {@code dummy} fallback API
 * key: Anthropic rejecting the key does not no-op, it throws.
 */
@Testcontainers
@SpringBootTest
@ActiveProfiles("test")
class LlmSummaryServiceAnthropicContractIT extends AbstractPostgresIT {

    @Container
    @ServiceConnection
    static final MongoDBContainer MONGO = new MongoDBContainer("mongo:7");

    @RegisterExtension
    static final WireMockExtension WM = WireMockExtension.newInstance()
            .options(wireMockConfig().port(8092))
            .build();

    @Autowired
    LlmSummaryService llmSummaryService;

    @Autowired
    TaxpayerReadModelRepository readModelRepository;

    @BeforeEach
    void seedTaxpayer() {
        readModelRepository.save(new TaxpayerReadModel("anthropic-contract-1", "Ada Lovelace", "SINGLE", "FEDERAL",
                Instant.now(), List.of(new EmbeddedLiability(2026, "fed-2026-10pct",
                        new BigDecimal("9500.00"), new BigDecimal("950.00"), Instant.now()))));
    }

    @AfterEach
    void cleanUp() {
        readModelRepository.deleteAll();
    }

    @Test
    void summarize_parsesTheRealAnthropicWireFormat_whenTheApiRespondsSuccessfully() {
        WM.stubFor(post(urlEqualTo("/v1/messages"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "id": "msg_test123",
                                  "type": "message",
                                  "role": "assistant",
                                  "model": "claude-sonnet-4-5",
                                  "content": [
                                    {
                                      "type": "text",
                                      "text": "{\\"filingStatus\\":\\"SINGLE\\",\\"totalLiability\\":950.0,\\"jurisdictionCount\\":1,\\"riskBand\\":\\"LOW\\"}"
                                    }
                                  ],
                                  "stop_reason": "end_turn",
                                  "stop_sequence": null,
                                  "usage": { "input_tokens": 120, "output_tokens": 24 }
                                }
                                """)));

        TaxpayerSummary summary = llmSummaryService.summarize("anthropic-contract-1");

        assertThat(summary).isEqualTo(new TaxpayerSummary("SINGLE", 950.0, 1, "LOW"));
    }

    @Test
    void summarize_throwsRatherThanSilentlyNoOps_whenAnthropicRejectsTheApiKey() {
        WM.stubFor(post(urlEqualTo("/v1/messages"))
                .willReturn(aResponse()
                        .withStatus(401)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "type": "error",
                                  "error": { "type": "authentication_error", "message": "invalid x-api-key" }
                                }
                                """)));

        assertThatThrownBy(() -> llmSummaryService.summarize("anthropic-contract-1"))
                .isInstanceOf(RestClientException.class);
    }
}
