package com.uptimecrew.tax_liability.lambda;

import java.lang.management.ManagementFactory;
import java.net.URI;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicBoolean;

import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.RequestHandler;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPEvent;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPResponse;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;

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

    /** The single EMF dimension key; also a top-level member of the document, as EMF requires. */
    private static final String DIMENSION_STAGE = "Stage";

    /** Cap on a caller-supplied correlation id. Long enough for a UUID or an X-Ray trace id. */
    private static final int MAX_CORRELATION_ID_LENGTH = 128;

    // --- INIT PHASE: everything below runs once per execution environment. -------------------

    private static final Logger LOG = LoggerFactory.getLogger(TaxpayerLookupHandler.class);

    private static final DynamoDbClient DDB = buildDynamoClient();

    private static final ObjectMapper JSON = newObjectMapper();

    /** Table name, injected by template.yaml as {@code !Ref TaxpayersTable}. Never hardcoded. */
    private static final String TABLE = System.getenv("TAXPAYERS_TABLE");

    private static final String METRICS_NAMESPACE =
            Optional.ofNullable(System.getenv("METRICS_NAMESPACE")).orElse(DEFAULT_METRICS_NAMESPACE);

    /** Dimension value on every emitted metric; {@code ENV} is {@code !Ref StageName}. */
    private static final String STAGE = Optional.ofNullable(System.getenv("ENV")).orElse("unknown");

    /**
     * Flips false on the first invocation in this execution environment, so the INIT cost is
     * reported exactly once rather than on every warm call.
     */
    private static final AtomicBoolean COLD_START = new AtomicBoolean(true);

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
        if (COLD_START.compareAndSet(true, false)) {
            reportColdStart();
        }

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
        } catch (RuntimeException e) {
            // Letting any of these propagate would end the invocation as an unhandled Lambda
            // error, and API Gateway turns that into an opaque 5xx with no body and, critically,
            // no x-correlation-id header - losing the trace at exactly the point the caller most
            // needs it. Confirmed against `sam local invoke`, where an invalid token surfaced as
            // a raw DynamoDbException stack trace instead of an HTTP response.
            // RuntimeException, not an enumerated list. The list was SdkException |
            // IllegalStateException | IllegalArgumentException, and an ArithmeticException from
            // BigDecimal.intValueExact() (a row with a fractional taxYear) slipped straight past
            // it - reintroducing the exact bug this catch exists to prevent. Any failure mapping
            // a stored row is a data problem, and every one of them should become a shaped 500
            // that still names the request rather than an opaque 5xx with no correlation id.
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
     * Logs the INIT cost once per execution environment.
     *
     * <p>JVM uptime at the first invocation stands in for the phase AWS reports as
     * {@code Init Duration}, which the local Runtime Interface Emulator does not populate (it
     * reports ~0.01ms and folds the cost into {@code Duration}) - so without this there is no way
     * to separate startup cost from handler cost off-AWS.
     *
     * <p><strong>It is deliberately not reported on a SnapStart restore.</strong> The snapshot is
     * taken after INIT, so a restored environment resumes a JVM whose start time predates the
     * snapshot: {@code getUptime()} would include however long the snapshot sat in storage, which
     * could be hours. AWS reports {@code Restore Duration} on the REPORT line for that case, and
     * that is the number to read.
     *
     * <p>{@code AWS_LAMBDA_INITIALIZATION_TYPE} is read here rather than cached in a static field
     * for the same reason the metric is suppressed: a static read happens during INIT, before the
     * snapshot, so it would be frozen as {@code on-demand} and every restored environment would
     * report the wrong type.
     */
    private static void reportColdStart() {
        String initType = Optional.ofNullable(System.getenv("AWS_LAMBDA_INITIALIZATION_TYPE"))
                .orElse("unknown");
        if ("snap-start".equals(initType)) {
            LOG.info("cold start initializationType={} (init duration not reported; "
                    + "read Restore Duration from the REPORT line)", initType);
        } else {
            LOG.info("cold start initializationType={} initDurationMs={}",
                    initType, ManagementFactory.getRuntimeMXBean().getUptime());
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
                .map(TaxpayerLookupHandler::sanitiseCorrelationId)
                .orElse(null);
        if (fromHeader != null && !fromHeader.isBlank()) {
            return fromHeader;
        }
        // getAwsRequestId() is never null on a real invocation, but a null here would reach
        // Map.of() when the response headers are built, and Map.of rejects null values - turning
        // a missing id into a NullPointerException instead of a degraded response.
        String requestId = ctx == null ? null : ctx.getAwsRequestId();
        return requestId == null || requestId.isBlank() ? "no-context" : requestId;
    }

    /**
     * Constrains a caller-supplied correlation id to characters that are safe everywhere it is
     * subsequently placed.
     *
     * <p>This value arrives in a request header, so it is attacker-controlled, and it ends up in
     * three places that all trust it: an HTTP response header, a JSON response body, and the EMF
     * metric document. Serialising the latter two through Jackson (see {@link #errorResponse} and
     * {@link #buildEmf}) already prevents structural injection, but a correlation id has no
     * legitimate need for quotes, braces or control characters, and a CR/LF would still be a
     * header-splitting attempt. Constraining it at the boundary is the cheaper guarantee.
     *
     * @param raw the header value; may be null
     * @return the id with unsafe characters removed and length capped, or {@code null} if nothing
     *         usable remains - in which case the caller falls back to the Lambda request id
     */
    private static String sanitiseCorrelationId(String raw) {
        if (raw == null) {
            return null;
        }
        // Deliberately an allow-list. A deny-list of "dangerous" characters is the thing that
        // gets outgrown; the set below covers every id format actually in use here (UUIDs,
        // `ci-smoke-<epoch>`, `probe-123`, X-Ray trace ids).
        String cleaned = raw.replaceAll("[^A-Za-z0-9_.:@=+/-]", "");
        if (cleaned.length() > MAX_CORRELATION_ID_LENGTH) {
            cleaned = cleaned.substring(0, MAX_CORRELATION_ID_LENGTH);
        }
        return cleaned.isBlank() ? null : cleaned;
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
        String body;
        try {
            // Serialised, NOT concatenated. correlationId originates in a request header, and
            // string-building this document let a caller sending `","x":"` inject arbitrary keys
            // into the response body.
            // LinkedHashMap, not Map.of: Map.of iteration order is unspecified and would make
            // the error body's key order vary between invocations for no reason.
            Map<String, String> payload = new LinkedHashMap<>();
            payload.put("error", msg);
            payload.put("correlationId", correlationId);
            body = JSON.writeValueAsString(payload);
        } catch (JsonProcessingException e) {
            // A constant, so this cannot recurse back into errorResponse the way calling the
            // serialiser again would.
            LOG.error("could not serialise error body", e);
            body = "{\"error\":\"internal error\"}";
        }
        return APIGatewayV2HTTPResponse.builder()
                .withStatusCode(status)
                .withHeaders(Map.of(
                        "Content-Type", "application/json",
                        CORRELATION_ID_HEADER, correlationId))
                .withBody(body)
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
        // Built as a map and serialised, NOT concatenated. correlationId originates in a request
        // header: with string-building, a caller sending
        //   x-correlation-id: ","TaxpayerLookupSuccess":999,"junk":"
        // injected a second TaxpayerLookupSuccess key into the document and could forge the
        // metric value, since duplicate-key resolution is parser-defined. An unbalanced quote was
        // worse still - invalid JSON, and CloudWatch drops the metric with no error anywhere.
        //
        // A library (aws-lambda-powertools-metrics) is still not warranted for two counters, but
        // hand-assembling the *structure* was the wrong economy; only the shape is ours now.
        Map<String, Object> directive = Map.of(
                "Namespace", METRICS_NAMESPACE,
                "Dimensions", List.of(List.of(DIMENSION_STAGE)),
                "Metrics", List.of(Map.of("Name", metricName, "Unit", "Count")));

        // LinkedHashMap: EMF does not require key order, but keeping _aws first keeps the line
        // readable in a log tail, which is where these are actually eyeballed.
        Map<String, Object> document = new LinkedHashMap<>();
        document.put("_aws", Map.of("Timestamp", timestampMs, "CloudWatchMetrics", List.of(directive)));
        document.put(DIMENSION_STAGE, STAGE);
        document.put("correlationId", correlationId);
        document.put(metricName, 1);

        try {
            return JSON.writeValueAsString(document);
        } catch (JsonProcessingException e) {
            LOG.error("could not serialise EMF payload for metric={}", metricName, e);
            return null;
        }
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
    /**
     * Builds the response serialiser.
     *
     * <p>Package-private and separate from the field so the wire contract can be asserted in a
     * test. Both settings below are load-bearing and both fail *silently* if wrong - a wrongly
     * configured mapper still produces valid JSON, just with the wrong shape for money or
     * timestamps, which no compiler or smoke test would catch.
     *
     * @return a mapper that writes {@link java.time.Instant} as ISO-8601 and preserves
     *         {@link java.math.BigDecimal} scale
     */
    static ObjectMapper newObjectMapper() {
        return new ObjectMapper()
                // Explicit module, NOT findAndRegisterModules(): that scans every jar on the
                // classpath at INIT to discover modules we already know we want. Registering it
                // by hand also means the timestamp format cannot quietly change because some
                // other dependency put a competing module on the classpath.
                .registerModule(new JavaTimeModule())
                // Instant as an ISO-8601 string, not a float epoch: the k3d/REST side of this
                // same capstone already serialises timestamps that way, and the contract should
                // not change just because the deployment shape did.
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    /**
     * @param values candidate values, in precedence order; any may be null
     * @return the first value that is neither null nor blank, or {@code null} if there is none.
     *         Blank counts as absent on purpose - an env var declared with an empty default is
     *         set-but-meaningless, and {@code Optional.ofNullable("")} would wrongly treat it as
     *         a real value and stop the search.
     */
    private static String firstNonBlank(String... values) {
        for (String v : values) {
            if (v != null && !v.isBlank()) {
                return v;
            }
        }
        return null;
    }

    private static DynamoDbClient buildDynamoClient() {
        DynamoDbClientBuilder builder = DynamoDbClient.builder()
                .httpClient(UrlConnectionHttpClient.create());
        // AWS_REGION is set by the Lambda runtime on every execution environment; falling back to
        // the SDK's own provider chain (profile/config file) covers `sam local invoke`.
        String region = System.getenv("AWS_REGION");
        if (region != null && !region.isBlank()) {
            builder.region(Region.of(region));
        }
        // Point DynamoDB at a local emulator when asked.
        //
        // DYNAMODB_ENDPOINT_OVERRIDE is deliberately OUR name rather than the AWS-standard
        // AWS_ENDPOINT_URL_DYNAMODB, and that is not arbitrary. `sam local invoke --env-vars`
        // can only override variables template.yaml already declares - undeclared ones are
        // dropped silently - so the variable has to appear in the template to be usable locally.
        // But declaring the *AWS-standard* name with an empty default breaks the deployed
        // function outright: the SDK reads that variable itself, and an empty value makes it
        // build an endpoint with no scheme, failing every call with
        // "SdkClientException: Unable to marshall request to JSON: protocol must not be null".
        // A name only this code reads can be declared empty harmlessly. The AWS-standard names
        // are still honoured as fallbacks for anyone who sets them properly.
        // Deliberately NOT falling back to the global AWS_ENDPOINT_URL: that variable redirects
        // every service, so anything setting it for an unrelated reason would silently reroute
        // DynamoDB traffic too. The service-specific name is the only safe one to honour, and the
        // SDK reads it natively anyway - this call is what lets `sam local invoke` inject an
        // override through a name the template declares.
        String endpoint = firstNonBlank(
                System.getenv("DYNAMODB_ENDPOINT_OVERRIDE"),
                System.getenv("AWS_ENDPOINT_URL_DYNAMODB"));
        if (endpoint != null) {
            LOG.info("using DynamoDB endpoint override endpoint={}", endpoint);
            builder.endpointOverride(URI.create(endpoint));
        }
        try {
            return builder.build();
        } catch (RuntimeException e) {
            LOG.warn("no DynamoDB client at INIT (no resolvable region); DynamoDB paths will fail fast", e);
            return null;
        }
    }
}
