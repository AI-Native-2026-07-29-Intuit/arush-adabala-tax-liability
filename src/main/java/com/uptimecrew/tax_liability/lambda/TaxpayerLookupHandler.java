package com.uptimecrew.tax_liability.lambda;

import java.util.Map;
import java.util.Optional;

import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.RequestHandler;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPEvent;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPResponse;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;

import software.amazon.awssdk.core.exception.SdkException;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.DynamoDbClientBuilder;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.GetItemRequest;
import software.amazon.awssdk.services.dynamodb.model.GetItemResponse;

/**
 * AWS Lambda handler for {@code GET /taxpayers/{taxpayerId}} (W5 D4) - the read side of the
 * capstone's taxpayer lookup, re-shipped from the long-lived k3d Deployment of W5 D3 as a single
 * function behind an API Gateway HTTP API.
 *
 * <p><strong>Every expensive object is a {@code private static final} field.</strong> Static
 * initialisers run once per execution environment, during the INIT phase, which is billed
 * differently from invocation time and - once SnapStart is enabled - is captured into the restored
 * snapshot so warm and restored invocations never pay it again. Building the
 * {@link DynamoDbClient} (TLS handshake plumbing, credential-provider chain) inside
 * {@link #handleRequest} instead would turn a ~50ms warm invocation into a multi-second one on
 * every single request.
 *
 * <p>Response contract:
 * <ul>
 *   <li><strong>400</strong> - no {@code taxpayerId} path parameter</li>
 *   <li><strong>404</strong> - no item in the table for that id</li>
 *   <li><strong>200</strong> - the JSON-serialised {@link TaxpayerRecord}</li>
 *   <li><strong>500</strong> - DynamoDB or serialisation failure</li>
 * </ul>
 *
 * <p>Every response, success or error, carries an {@code x-correlation-id} header and every log
 * line carries the same value, so one request can be followed across API Gateway, this function,
 * and whatever called it. See {@link #resolveCorrelationId}.
 */
public final class TaxpayerLookupHandler implements RequestHandler<APIGatewayV2HTTPEvent, APIGatewayV2HTTPResponse> {

    /** Request/response header the correlation id travels on. */
    static final String CORRELATION_ID_HEADER = "x-correlation-id";

    /** Fallback namespace when {@code METRICS_NAMESPACE} is unset (i.e. outside the SAM stack). */
    private static final String DEFAULT_METRICS_NAMESPACE = "TaxcalcDev";

    // --- INIT PHASE: everything below runs once per execution environment. -------------------

    private static final Logger LOG = LoggerFactory.getLogger(TaxpayerLookupHandler.class);

    private static final DynamoDbClient DDB = buildDynamoClient();

    private static final ObjectMapper JSON = new ObjectMapper()
            .findAndRegisterModules()
            // Instant as an ISO-8601 string, not a float epoch: the k3d/REST side of this same
            // capstone already serialises timestamps that way, and the contract should not change
            // just because the deployment shape did.
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);

    /** Table name, injected by template.yaml as {@code !Ref TaxpayersTable}. Never hardcoded. */
    private static final String TABLE = System.getenv("TAXPAYERS_TABLE");

    private static final String METRICS_NAMESPACE =
            Optional.ofNullable(System.getenv("METRICS_NAMESPACE")).orElse(DEFAULT_METRICS_NAMESPACE);

    /** Dimension value on every emitted metric; {@code ENV} is {@code !Ref StageName}. */
    private static final String STAGE = Optional.ofNullable(System.getenv("ENV")).orElse("unknown");

    /**
     * Handles one API Gateway HTTP API (payload format 2.0) request.
     *
     * @param event the proxy event; its {@code pathParameters} must carry {@code taxpayerId}
     * @param ctx   the Lambda context, used for the correlation-id fallback and remaining budget
     * @return a 200/400/404/500 response, always with an {@code x-correlation-id} header
     */
    @Override
    public APIGatewayV2HTTPResponse handleRequest(APIGatewayV2HTTPEvent event, Context ctx) {
        String correlationId = resolveCorrelationId(event, ctx);
        // MDC, not a {} placeholder in every message: the JsonTemplateLayout promotes each
        // thread-context key to a top-level field, so correlationId becomes something Logs
        // Insights can filter on (`fields correlationId | filter correlationId = "..."`) instead
        // of a substring that has to be regex-matched out of the message text. Cleared in the
        // finally block because execution environments - and therefore threads - are reused
        // across invocations, and a leaked id would mislabel the next request's logs.
        MDC.put("correlationId", correlationId);
        try {
            return handleRequestInternal(event, ctx, correlationId);
        } finally {
            MDC.remove("correlationId");
        }
    }

    private APIGatewayV2HTTPResponse handleRequestInternal(
            APIGatewayV2HTTPEvent event, Context ctx, String correlationId) {
        String taxpayerId = Optional.ofNullable(event)
                .map(APIGatewayV2HTTPEvent::getPathParameters)
                .map(p -> p.get("taxpayerId"))
                .orElse(null);

        LOG.info("lookup attempt taxpayerId={} remainingMs={}",
                taxpayerId, ctx == null ? -1 : ctx.getRemainingTimeInMillis());

        if (taxpayerId == null || taxpayerId.isBlank()) {
            return errorResponse(400, "missing taxpayerId path parameter", correlationId);
        }

        TaxpayerRecord record;
        try {
            record = loadFromDynamo(taxpayerId);
        } catch (SdkException | IllegalStateException | IllegalArgumentException e) {
            // Letting any of these propagate would end the invocation as an unhandled Lambda
            // error, and API Gateway turns that into an opaque 5xx with no body and, critically,
            // no x-correlation-id header - losing the trace at exactly the point the caller most
            // needs it. Confirmed against `sam local invoke`, where an invalid token surfaced as
            // a raw DynamoDbException stack trace instead of an HTTP response.
            // IllegalArgumentException covers the other half: a row written with a missing or
            // mistyped attribute is a data problem, not a client problem, so it is a 500 too -
            // but a *shaped* one that still says which request it happened on.
            LOG.error("dynamodb lookup failed taxpayerId={}", taxpayerId, e);
            return errorResponse(500, "taxpayer lookup failed", correlationId);
        }
        if (record == null) {
            emitMetric("TaxpayerNotFound", correlationId);
            return errorResponse(404, "taxpayer not found", correlationId);
        }

        try {
            String body = JSON.writeValueAsString(record);
            emitMetric("TaxpayerLookupSuccess", correlationId);
            LOG.info("lookup hit taxpayerId={} liabilities={}",
                    taxpayerId, record.getLiabilities().size());
            return APIGatewayV2HTTPResponse.builder()
                    .withStatusCode(200)
                    .withHeaders(Map.of(
                            "Content-Type", "application/json",
                            CORRELATION_ID_HEADER, correlationId))
                    .withBody(body)
                    .build();
        } catch (Exception e) {
            LOG.error("serialisation failure", e);
            return errorResponse(500, "serialisation failure", correlationId);
        }
    }

    /**
     * Resolves the id this request is traced by: the caller's {@code x-correlation-id} header when
     * present, otherwise the Lambda request id.
     *
     * <p>Caller-supplied wins deliberately. When the browser or an upstream service already opened
     * a trace, adopting its id keeps the whole hop joinable; minting a fresh one here would break
     * the chain at exactly the boundary it is most needed. Header lookup is case-insensitive
     * because API Gateway lower-cases header names on the v2 payload but {@code sam local invoke}
     * replays whatever casing the sample event file uses.
     *
     * @param event the incoming event; may be null or header-less
     * @param ctx   the Lambda context, used for the {@code getAwsRequestId()} fallback
     * @return a non-null correlation id
     */
    static String resolveCorrelationId(APIGatewayV2HTTPEvent event, Context ctx) {
        String fromHeader = Optional.ofNullable(event)
                .map(APIGatewayV2HTTPEvent::getHeaders)
                .map(TaxpayerLookupHandler::findCorrelationHeader)
                .orElse(null);
        if (fromHeader != null && !fromHeader.isBlank()) {
            return fromHeader;
        }
        return ctx == null ? "no-context" : ctx.getAwsRequestId();
    }

    private static String findCorrelationHeader(Map<String, String> headers) {
        for (Map.Entry<String, String> entry : headers.entrySet()) {
            if (entry.getKey() != null && CORRELATION_ID_HEADER.equalsIgnoreCase(entry.getKey())) {
                return entry.getValue();
            }
        }
        return null;
    }

    /**
     * Issues a single strongly-typed {@code GetItem} against the read model.
     *
     * @param id the partition key value
     * @return the mapped record, or {@code null} when the table has no such item
     * @throws IllegalStateException if {@code TAXPAYERS_TABLE} is unset or no DynamoDB client
     *                               could be built (i.e. the function is misconfigured)
     */
    private TaxpayerRecord loadFromDynamo(String id) {
        if (TABLE == null || TABLE.isBlank()) {
            throw new IllegalStateException("TAXPAYERS_TABLE env var is not set");
        }
        if (DDB == null) {
            throw new IllegalStateException("no DynamoDB client: AWS region could not be resolved at INIT");
        }
        GetItemRequest req = GetItemRequest.builder()
                .tableName(TABLE)
                .key(Map.of("id", AttributeValue.builder().s(id).build()))
                // Eventually-consistent: this is a read-only lookup of a projection that is
                // itself asynchronously updated, and a consistent read costs double the RCUs
                // for a freshness guarantee the upstream pipeline cannot provide anyway.
                .consistentRead(false)
                .build();
        GetItemResponse resp = DDB.getItem(req);
        if (!resp.hasItem() || resp.item().isEmpty()) {
            return null;
        }
        return TaxpayerRecord.fromItem(resp.item());
    }

    private APIGatewayV2HTTPResponse errorResponse(int status, String msg, String correlationId) {
        LOG.warn("error response status={} msg={}", status, msg);
        return APIGatewayV2HTTPResponse.builder()
                .withStatusCode(status)
                .withHeaders(Map.of(
                        "Content-Type", "application/json",
                        CORRELATION_ID_HEADER, correlationId))
                .withBody("{\"error\":\"" + msg + "\",\"correlationId\":\"" + correlationId + "\"}")
                .build();
    }

    /**
     * Publishes one custom CloudWatch metric using the embedded metric format (EMF).
     *
     * <p>EMF rather than a {@code cloudwatch:PutMetricData} call for two reasons: PutMetricData is
     * a synchronous, network-bound API call that adds its own latency to every invocation, and it
     * would force a second IAM permission onto an execution role this deliverable deliberately
     * scopes to one DynamoDB table. An EMF line is just structured output - CloudWatch extracts
     * the metric from the log event asynchronously, at no invocation cost and no extra permission.
     *
     * <p>Written straight to {@code System.out} rather than through SLF4J so nothing wraps or
     * re-encodes the payload on the way out: the EMF parser looks for {@code _aws} at the root of
     * the emitted object.
     *
     * @param metricName    the metric to increment by one
     * @param correlationId echoed into the blob as a searchable property (not a dimension - a
     *                      per-request dimension value would mint a distinct metric per request,
     *                      which is both useless and billable)
     */
    private void emitMetric(String metricName, String correlationId) {
        System.out.println(buildEmf(metricName, correlationId, System.currentTimeMillis()));
    }

    /**
     * Builds the EMF payload. Split out of {@link #emitMetric} and given an injected timestamp
     * purely so it is testable: this is the one piece of hand-assembled JSON in the codebase, and
     * a single missing brace would fail silently - CloudWatch would accept the log line and simply
     * never publish a metric, with nothing anywhere reporting an error.
     *
     * @param metricName    the metric name, which is also the key holding its value
     * @param correlationId the request's correlation id
     * @param timestampMs   epoch millis EMF stamps the datapoint with
     * @return a single-line EMF document
     */
    static String buildEmf(String metricName, String correlationId, long timestampMs) {
        // Hand-written rather than pulled in via aws-lambda-powertools-metrics: that library's
        // ergonomic path is an @Metrics annotation woven by aspectj, i.e. a build plugin plus a
        // runtime weaving agent added to a function whose entire metric surface is two counters.
        return "{\"_aws\":{\"Timestamp\":" + timestampMs
                + ",\"CloudWatchMetrics\":[{\"Namespace\":\"" + METRICS_NAMESPACE
                + "\",\"Dimensions\":[[\"Stage\"]],\"Metrics\":[{\"Name\":\"" + metricName
                + "\",\"Unit\":\"Count\"}]}]},\"Stage\":\"" + STAGE
                + "\",\"correlationId\":\"" + correlationId
                + "\",\"" + metricName + "\":1}";
    }

    /**
     * Builds the DynamoDB client for the INIT phase.
     *
     * <p>The HTTP client is set explicitly because {@code pom.xml} excludes both of the SDK's
     * default implementations (Netty and Apache): without an explicit choice the SDK fails at
     * build time with "Unable to load an HTTP implementation". {@code UrlConnectionHttpClient}
     * has no connection pool or event loop to warm up, which is the right trade for a function
     * that makes exactly one blocking call per invocation.
     *
     * @return a client, or {@code null} when no region can be resolved - which only happens off
     *         Lambda (unit tests, CI), where returning null lets the non-DynamoDB paths still be
     *         exercised instead of failing the whole class at static-init time
     */
    private static DynamoDbClient buildDynamoClient() {
        DynamoDbClientBuilder builder = DynamoDbClient.builder()
                .httpClient(UrlConnectionHttpClient.create());
        // AWS_REGION is set by the Lambda runtime on every execution environment; falling back to
        // the SDK's own provider chain (profile/config file) covers `sam local invoke`.
        String region = System.getenv("AWS_REGION");
        if (region != null && !region.isBlank()) {
            builder.region(Region.of(region));
        }
        try {
            return builder.build();
        } catch (RuntimeException e) {
            LOG.warn("no DynamoDB client at INIT (no resolvable region); DynamoDB paths will fail fast", e);
            return null;
        }
    }
}
