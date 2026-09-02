package com.uptimecrew.tax_liability.lambda;

import java.util.Map;

import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPEvent;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPResponse;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.Mockito;

import software.amazon.awssdk.awscore.exception.AwsServiceException;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.GetItemRequest;
import software.amazon.awssdk.services.dynamodb.model.GetItemResponse;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Unit tests for {@link TaxpayerLookupHandler}'s request-shaping contract (W5 D4).
 *
 * <p>Covers every branch of {@link TaxpayerLookupHandler#handleRequest}: the path-parameter guard
 * (400), a hit (200), a miss (404), and a DynamoDB failure (500), plus correlation-id propagation
 * and the EMF payload. The DynamoDB-backed branches use a mocked {@link DynamoDbClient} injected
 * through the package-private constructor.
 *
 * <p>An earlier version of this class tested only the 400 path and claimed the rest was "covered
 * by scripts/sam-smoke.sh against a real deployed stack". That was a rationalisation of a design
 * limitation - the client was a static built at class-initialisation with no seam to substitute -
 * and the smoke script has never run against a real deployed stack. Both halves are fixed here.
 */
class TaxpayerLookupHandlerTest {

    private static final String TABLE = "taxpayers-test";

    /** Exercises the no-arg constructor the Lambda runtime actually uses. */
    private final TaxpayerLookupHandler handler = new TaxpayerLookupHandler();

    private DynamoDbClient dynamo;

    /** Same handler, but reading through a mock so the DynamoDB branches are reachable. */
    private TaxpayerLookupHandler handlerWithDynamo;

    private Context ctx;

    @BeforeEach
    void setUp() {
        ctx = Mockito.mock(Context.class);
        Mockito.when(ctx.getAwsRequestId()).thenReturn("aws-req-x");
        Mockito.when(ctx.getRemainingTimeInMillis()).thenReturn(9_000);
        dynamo = Mockito.mock(DynamoDbClient.class);
        handlerWithDynamo = new TaxpayerLookupHandler(dynamo, TABLE);
    }

    private static APIGatewayV2HTTPEvent lookupOf(String taxpayerId) {
        APIGatewayV2HTTPEvent event = new APIGatewayV2HTTPEvent();
        event.setPathParameters(Map.of("taxpayerId", taxpayerId));
        return event;
    }

    private static Map<String, AttributeValue> storedItem() {
        return Map.of(
                "id", AttributeValue.builder().s("txp_synth_001").build(),
                "displayName", AttributeValue.builder().s("Synthetic Taxpayer One").build(),
                "filingStatus", AttributeValue.builder().s("SINGLE").build(),
                "homeJurisdiction", AttributeValue.builder().s("CA").build(),
                "createdAt", AttributeValue.builder().s("2026-01-15T10:30:00Z").build(),
                "liabilities", AttributeValue.builder().l(
                        AttributeValue.builder().m(Map.of(
                                "taxYear", AttributeValue.builder().n("2025").build(),
                                "bracketId", AttributeValue.builder().s("brk-fed-2025-22").build(),
                                "taxableAmount", AttributeValue.builder().n("85000.00").build(),
                                "liabilityAmount", AttributeValue.builder().n("14235.50").build(),
                                "computedAt", AttributeValue.builder().s("2026-01-20T08:00:00Z").build()))
                                .build()).build());
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

    // --- The DynamoDB-backed branches: 200, 404, 500 -------------------------------------

    @Test
    void returns200WithTheSerialisedRecordWhenTheItemExists() {
        Mockito.when(dynamo.getItem(Mockito.any(GetItemRequest.class)))
                .thenReturn(GetItemResponse.builder().item(storedItem()).build());

        APIGatewayV2HTTPResponse resp = handlerWithDynamo.handleRequest(lookupOf("txp_synth_001"), ctx);

        assertThat(resp.getStatusCode()).isEqualTo(200);
        assertThat(resp.getHeaders()).containsEntry("Content-Type", "application/json");
        // The wire contract, on the bytes the caller actually receives: scale-2 money with its
        // trailing zero, and ISO-8601 timestamps rather than float epochs.
        assertThat(resp.getBody())
                .contains("\"id\":\"txp_synth_001\"")
                .contains("\"taxableAmount\":85000.00")
                .contains("\"liabilityAmount\":14235.50")
                .contains("\"totalLiability\":14235.50")
                .contains("\"createdAt\":\"2026-01-15T10:30:00Z\"");
    }

    @Test
    void issuesExactlyOneGetItemAgainstTheConfiguredTable() {
        Mockito.when(dynamo.getItem(Mockito.any(GetItemRequest.class)))
                .thenReturn(GetItemResponse.builder().item(storedItem()).build());

        handlerWithDynamo.handleRequest(lookupOf("txp_synth_001"), ctx);

        ArgumentCaptor<GetItemRequest> captor = ArgumentCaptor.forClass(GetItemRequest.class);
        // "a single GetItem on the table by id" is a stated requirement, so assert the call
        // itself, not just the response it produced.
        Mockito.verify(dynamo, Mockito.times(1)).getItem(captor.capture());
        GetItemRequest req = captor.getValue();
        assertThat(req.tableName()).isEqualTo(TABLE);
        assertThat(req.key()).containsOnlyKeys("id");
        assertThat(req.key().get("id").s()).isEqualTo("txp_synth_001");
        // Eventually-consistent on purpose: a consistent read doubles the RCU cost for a
        // freshness guarantee the upstream projection cannot provide anyway.
        assertThat(req.consistentRead()).isFalse();
    }

    @Test
    void returns404WhenTheTableHasNoSuchItem() {
        Mockito.when(dynamo.getItem(Mockito.any(GetItemRequest.class)))
                .thenReturn(GetItemResponse.builder().build());   // hasItem() == false

        APIGatewayV2HTTPResponse resp = handlerWithDynamo.handleRequest(
                lookupOf("missing-id"), ctx);

        assertThat(resp.getStatusCode()).isEqualTo(404);
        assertThat(resp.getBody()).contains("taxpayer not found");
        // Even the miss carries the correlation id, so a caller can join the trace.
        assertThat(resp.getHeaders())
                .containsEntry(TaxpayerLookupHandler.CORRELATION_ID_HEADER, "aws-req-x");
    }

    @Test
    void returns404RatherThanAnEmptyBodyWhenTheItemMapIsPresentButEmpty() {
        // DynamoDB can answer with an item map that is present but empty; treating that as a hit
        // would serialise a record with no id and return 200.
        Mockito.when(dynamo.getItem(Mockito.any(GetItemRequest.class)))
                .thenReturn(GetItemResponse.builder().item(Map.of()).build());

        APIGatewayV2HTTPResponse resp = handlerWithDynamo.handleRequest(lookupOf("ghost"), ctx);

        assertThat(resp.getStatusCode()).isEqualTo(404);
    }

    @Test
    void returns500WhenDynamoFailsRatherThanLettingTheExceptionEscape() {
        Mockito.when(dynamo.getItem(Mockito.any(GetItemRequest.class)))
                .thenThrow(AwsServiceException.builder().message("throttled").build());

        APIGatewayV2HTTPResponse resp = handlerWithDynamo.handleRequest(
                lookupOf("txp_synth_001"), ctx);

        // An escaping exception ends the invocation as an unhandled Lambda error, which API
        // Gateway renders as an opaque 5xx with no body and no correlation id.
        assertThat(resp.getStatusCode()).isEqualTo(500);
        assertThat(resp.getBody()).contains("taxpayer lookup failed");
        assertThat(resp.getHeaders())
                .containsEntry(TaxpayerLookupHandler.CORRELATION_ID_HEADER, "aws-req-x");
    }

    @Test
    void returns500WhenAStoredRowIsMalformed() {
        Map<String, AttributeValue> broken = new java.util.HashMap<>(storedItem());
        broken.remove("filingStatus");
        Mockito.when(dynamo.getItem(Mockito.any(GetItemRequest.class)))
                .thenReturn(GetItemResponse.builder().item(broken).build());

        APIGatewayV2HTTPResponse resp = handlerWithDynamo.handleRequest(
                lookupOf("txp_synth_001"), ctx);

        // A bad row is a data problem, not a client problem - but it must still be a shaped 500
        // that names the request, not an unhandled error.
        assertThat(resp.getStatusCode()).isEqualTo(500);
        assertThat(resp.getBody()).contains("taxpayer lookup failed");
    }

    @Test
    void returns500WhenTheTableNameIsNotConfigured() {
        TaxpayerLookupHandler misconfigured = new TaxpayerLookupHandler(dynamo, null);

        APIGatewayV2HTTPResponse resp = misconfigured.handleRequest(lookupOf("txp_synth_001"), ctx);

        assertThat(resp.getStatusCode()).isEqualTo(500);
        Mockito.verify(dynamo, Mockito.never()).getItem(Mockito.any(GetItemRequest.class));
    }

    @Test
    void doesNotCallDynamoAtAllWhenThePathParameterIsMissing() {
        handlerWithDynamo.handleRequest(new APIGatewayV2HTTPEvent(), ctx);

        // The 400 guard must short-circuit before the read, or a malformed request costs an RCU.
        Mockito.verify(dynamo, Mockito.never()).getItem(Mockito.any(GetItemRequest.class));
    }

    @Test
    void hostileCorrelationIdCannotInjectKeysIntoTheEmfDocument() throws Exception {
        // The header is attacker-controlled. When this document was built by string concatenation,
        // this exact value injected a second "TaxpayerLookupSuccess": 999 key and let a caller
        // forge the metric value, since duplicate-key resolution is parser-defined.
        String hostile = "\",\"TaxpayerLookupSuccess\":999,\"junk\":\"";

        String emf = TaxpayerLookupHandler.buildEmf("TaxpayerLookupSuccess", hostile, 1_788_300_000_000L);
        JsonNode root = new ObjectMapper().readTree(emf);

        // The metric keeps the value WE set, and the hostile text stays inside a single string.
        assertThat(root.path("TaxpayerLookupSuccess").asInt()).isEqualTo(1);
        assertThat(root.path("correlationId").asText()).isEqualTo(hostile);
        assertThat(root.has("junk")).isFalse();
    }

    @Test
    void hostileCorrelationIdIsStrippedBeforeItReachesHeadersOrBody() {
        APIGatewayV2HTTPEvent event = new APIGatewayV2HTTPEvent();
        // Quotes would corrupt a concatenated JSON body; CR/LF is a header-splitting attempt.
        event.setHeaders(Map.of(TaxpayerLookupHandler.CORRELATION_ID_HEADER,
                "abc\",\"evil\":\"x\r\nX-Injected: 1"));

        APIGatewayV2HTTPResponse resp = handler.handleRequest(event, ctx);

        String echoed = resp.getHeaders().get(TaxpayerLookupHandler.CORRELATION_ID_HEADER);
        assertThat(echoed).doesNotContain("\"").doesNotContain("\r").doesNotContain("\n");
        // ':' survives on purpose - it is legal in a correlation id (X-Ray trace ids use it) and
        // harmless in both a header value and a JSON string. Quotes and CR/LF do not.
        assertThat(echoed).isEqualTo("abcevil:xX-Injected:1");
    }

    @Test
    void errorBodyStaysWellFormedJsonForAnyCorrelationId() throws Exception {
        APIGatewayV2HTTPEvent event = new APIGatewayV2HTTPEvent();
        event.setHeaders(Map.of(TaxpayerLookupHandler.CORRELATION_ID_HEADER, "probe-9"));

        APIGatewayV2HTTPResponse resp = handler.handleRequest(event, ctx);
        JsonNode body = new ObjectMapper().readTree(resp.getBody());

        assertThat(body.path("error").asText()).isEqualTo("missing taxpayerId path parameter");
        assertThat(body.path("correlationId").asText()).isEqualTo("probe-9");
    }

    @Test
    void fallsBackToNoContextWhenTheRequestIdIsNull() {
        // Map.of rejects null values, so a null request id used to surface as a
        // NullPointerException while the response headers were being built.
        Context nullIdCtx = Mockito.mock(Context.class);
        Mockito.when(nullIdCtx.getAwsRequestId()).thenReturn(null);
        Mockito.when(nullIdCtx.getRemainingTimeInMillis()).thenReturn(9_000);

        APIGatewayV2HTTPResponse resp = handler.handleRequest(new APIGatewayV2HTTPEvent(), nullIdCtx);

        assertThat(resp.getStatusCode()).isEqualTo(400);
        assertThat(resp.getHeaders())
                .containsEntry(TaxpayerLookupHandler.CORRELATION_ID_HEADER, "no-context");
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
