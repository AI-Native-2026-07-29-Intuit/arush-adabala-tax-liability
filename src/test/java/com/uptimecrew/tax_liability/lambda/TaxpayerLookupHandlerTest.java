package com.uptimecrew.tax_liability.lambda;

import java.util.Map;

import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPEvent;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPResponse;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Unit tests for {@link TaxpayerLookupHandler}'s request-shaping contract (W5 D4).
 *
 * <p>Deliberately covers only the paths that never touch DynamoDB - the path-parameter guard and
 * correlation-id propagation. Those are the two places contract drift is both most likely and
 * cheapest to catch: they run in milliseconds under {@code mvn test} and inside {@code sam build},
 * with no AWS round trip, no credentials, and no LocalStack. The DynamoDB read itself is covered
 * by {@code scripts/sam-smoke.sh} against a real deployed stack, where it is actually meaningful.
 */
class TaxpayerLookupHandlerTest {

    private final TaxpayerLookupHandler handler = new TaxpayerLookupHandler();

    private Context ctx;

    @BeforeEach
    void setUp() {
        ctx = Mockito.mock(Context.class);
        Mockito.when(ctx.getAwsRequestId()).thenReturn("aws-req-x");
        Mockito.when(ctx.getRemainingTimeInMillis()).thenReturn(9_000);
    }

    @Test
    void returns400OnMissingPathParam() {
        APIGatewayV2HTTPEvent event = new APIGatewayV2HTTPEvent();
        // No pathParameters set at all - the shape API Gateway sends when the route matched but
        // the greedy segment was empty.

        APIGatewayV2HTTPResponse resp = handler.handleRequest(event, ctx);

        assertThat(resp.getStatusCode()).isEqualTo(400);
        assertThat(resp.getBody()).contains("missing taxpayerId");
    }

    @Test
    void returns400WhenPathParamIsBlank() {
        APIGatewayV2HTTPEvent event = new APIGatewayV2HTTPEvent();
        event.setPathParameters(Map.of("taxpayerId", "   "));

        APIGatewayV2HTTPResponse resp = handler.handleRequest(event, ctx);

        // A blank id would otherwise reach DynamoDB and come back as a 404, which reads to the
        // caller like "no such taxpayer" rather than "you sent a malformed request".
        assertThat(resp.getStatusCode()).isEqualTo(400);
    }

    @Test
    void echoesCallerCorrelationIdWhenSupplied() {
        APIGatewayV2HTTPEvent event = new APIGatewayV2HTTPEvent();
        event.setHeaders(Map.of(TaxpayerLookupHandler.CORRELATION_ID_HEADER, "caller-corr-42"));

        APIGatewayV2HTTPResponse resp = handler.handleRequest(event, ctx);

        // Missing path param still 400, but the caller's correlation id MUST be echoed back even
        // on the error path - an error is exactly when the caller most needs to join the trace.
        assertThat(resp.getStatusCode()).isEqualTo(400);
        assertThat(resp.getHeaders())
                .containsEntry(TaxpayerLookupHandler.CORRELATION_ID_HEADER, "caller-corr-42");
        assertThat(resp.getBody()).contains("caller-corr-42");
    }

    @Test
    void echoesCallerCorrelationIdRegardlessOfHeaderCasing() {
        APIGatewayV2HTTPEvent event = new APIGatewayV2HTTPEvent();
        // API Gateway lower-cases header names on the v2 payload, but `sam local invoke` replays
        // the sample event verbatim - so the lookup cannot be case-sensitive.
        event.setHeaders(Map.of("X-Correlation-Id", "mixed-case-7"));

        APIGatewayV2HTTPResponse resp = handler.handleRequest(event, ctx);

        assertThat(resp.getHeaders())
                .containsEntry(TaxpayerLookupHandler.CORRELATION_ID_HEADER, "mixed-case-7");
    }

    @Test
    void fallsBackToAwsRequestIdWhenCallerSuppliesNoCorrelationId() {
        APIGatewayV2HTTPEvent event = new APIGatewayV2HTTPEvent();

        APIGatewayV2HTTPResponse resp = handler.handleRequest(event, ctx);

        assertThat(resp.getHeaders())
                .containsEntry(TaxpayerLookupHandler.CORRELATION_ID_HEADER, "aws-req-x");
    }

    @Test
    void emfPayloadIsWellFormedJsonWithAwsAtTheRoot() throws Exception {
        // The one piece of hand-assembled JSON in this codebase, and its failure mode is silent:
        // CloudWatch accepts a malformed EMF line as an ordinary log event and simply publishes
        // no metric, with nothing anywhere raising an error. So it gets parsed in a test.
        String emf = TaxpayerLookupHandler.buildEmf("TaxpayerLookupSuccess", "corr-9", 1_788_220_800_000L);

        JsonNode root = new ObjectMapper().readTree(emf);

        assertThat(root.has("_aws")).isTrue();
        assertThat(root.path("_aws").path("Timestamp").asLong()).isEqualTo(1_788_220_800_000L);
        JsonNode directive = root.path("_aws").path("CloudWatchMetrics").get(0);
        assertThat(directive.path("Namespace").asText()).isEqualTo("TaxcalcDev");
        assertThat(directive.path("Metrics").get(0).path("Name").asText()).isEqualTo("TaxpayerLookupSuccess");
        assertThat(directive.path("Metrics").get(0).path("Unit").asText()).isEqualTo("Count");
        // The dimension key must resolve to a real member of the same object, or CloudWatch drops
        // the whole directive.
        assertThat(directive.path("Dimensions").get(0).get(0).asText()).isEqualTo("Stage");
        assertThat(root.has("Stage")).isTrue();
        // The metric name doubles as the key holding the value.
        assertThat(root.path("TaxpayerLookupSuccess").asInt()).isEqualTo(1);
        assertThat(root.path("correlationId").asText()).isEqualTo("corr-9");
    }
}
