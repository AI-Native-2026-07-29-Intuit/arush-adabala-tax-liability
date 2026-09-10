package com.uptimecrew.tax_liability.llm.cost;

import java.io.UncheckedIOException;
import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Objects;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * One structured line per LLM call, in CloudWatch Embedded Metric Format (W6 D4 Task 2).
 *
 * <p><b>This is the LLM plane's entire cost attribution.</b> Anthropic spend bills to the
 * Anthropic workspace and never appears in AWS billing, so no AWS Budget, Cost Explorer report
 * or {@code EstimatedCharges} alarm can see it - not because they are misconfigured, but because
 * it is not AWS spend. The per-feature dollar figure exists only because it is written here.
 *
 * <p>EMF means the line is simultaneously a log record and a metric: wherever stdout ships to
 * CloudWatch Logs, the embedded {@code _aws} block causes {@code CostUsd} and {@code LatencyMs}
 * to be extracted as real metrics in namespace {@code uptimecrew/llmproxy}, dimensioned by
 * {@code [[service, tenant, feature]]}, with no metric-publishing call and no extra IAM
 * permission. Somewhere that does not ship stdout to CloudWatch, the same line is still a
 * perfectly queryable JSON log record - so this degrades to something useful rather than to
 * nothing.
 *
 * <p><b>The JSON is built with Jackson, not string concatenation.</b> The reference
 * implementation for this task concatenates the field values straight into a JSON literal, which
 * produces a malformed line the moment any of {@code tenant}, {@code feature} or {@code modelId}
 * contains a quote or a backslash - and {@code tenant} is caller-influenced data. A broken line
 * does not fail loudly: CloudWatch drops the malformed record's metrics and keeps the text, so
 * the cost series simply loses those calls and reads low. Serialising properly costs one
 * {@link ObjectMapper} and removes the whole class of failure.
 *
 * <p><b>{@code CostUsd} is emitted as a double, and that is not a money-storage decision.</b>
 * CloudWatch metric values are doubles at the API level, so the conversion happens somewhere no
 * matter what; doing it here, once, at the edge, from an exact integer means nothing accumulates
 * in floating point. The exact figure travels alongside as {@code CostUsdE5} (integer 1e-5 USD),
 * so any consumer that needs to sum costs without error has an exact field to sum.
 */
public class CostLogger {

    /** EMF namespace. Matches the value queried in taxcalc-api/COST.md. */
    public static final String NAMESPACE = "uptimecrew/llmproxy";

    private static final Logger LOG = LoggerFactory.getLogger(CostLogger.class);

    /** Divisor from integer 1e-5 units back to USD. */
    private static final double E5 = 100_000.0;

    private final ObjectMapper mapper;

    /** Uses a private {@link ObjectMapper}; the cost line's shape must not follow app config. */
    public CostLogger() {
        this(new ObjectMapper());
    }

    /**
     * @param mapper the mapper to serialise the EMF line with; never null
     * @throws NullPointerException if {@code mapper} is null
     */
    public CostLogger(ObjectMapper mapper) {
        this.mapper = Objects.requireNonNull(mapper, "mapper must not be null");
    }

    /**
     * Emit one EMF line for a completed LLM call.
     *
     * @param ctx        attribution context for the call; never null
     * @param resp       what the call returned; never null
     * @param costUsdE5  computed cost in integer units of 1e-5 USD; must not be negative
     * @throws NullPointerException     if {@code ctx} or {@code resp} is null
     * @throws IllegalArgumentException if {@code costUsdE5} is negative
     */
    public void emit(CallContext ctx, UpstreamResponse resp, long costUsdE5) {
        Objects.requireNonNull(ctx, "ctx must not be null");
        Objects.requireNonNull(resp, "resp must not be null");
        if (costUsdE5 < 0) {
            throw new IllegalArgumentException("costUsdE5 must not be negative, was " + costUsdE5);
        }
        LOG.info("{}", render(ctx, resp, costUsdE5));
    }

    /**
     * Build the EMF line without emitting it.
     *
     * <p>Package-private so {@code CostLoggerTest} can assert the exact document - dimension set,
     * metric names, field values - by parsing it, rather than by scraping a log appender and
     * regex-matching text.
     *
     * @return a single-line JSON document in CloudWatch Embedded Metric Format
     */
    String render(CallContext ctx, UpstreamResponse resp, long costUsdE5) {
        Map<String, Object> metricDirective = Map.of(
                "Timestamp", ctx.at().toEpochMilli(),
                "CloudWatchMetrics", List.of(Map.of(
                        "Namespace", NAMESPACE,
                        // A dimension set is a LIST of lists: this declares exactly one set, of
                        // three keys. CloudWatch treats each distinct combination of dimension
                        // values as its own metric series, so adding a high-cardinality key here
                        // (a request id, a taxpayer id) would mint a series per call and turn a
                        // cost-tracking feature into a bill of its own.
                        "Dimensions", List.of(List.of("service", "tenant", "feature")),
                        "Metrics", List.of(
                                Map.of("Name", "CostUsd", "Unit", "None"),
                                Map.of("Name", "LatencyMs", "Unit", "Milliseconds")))));

        Map<String, Object> line = new java.util.LinkedHashMap<>();
        line.put("_aws", metricDirective);
        // The three dimension VALUES must exist as top-level members, or CloudWatch discards the
        // metric while still storing the log line - green-looking output, no series.
        line.put("service", ctx.service());
        line.put("tenant", ctx.tenant());
        line.put("feature", ctx.feature());
        line.put("modelId", resp.modelId());
        // The dated snapshot the provider actually served, when it differs from the requested
        // alias. Without it a cost line cannot be reconciled against an invoice after the alias
        // has floated to a new snapshot - the log would say claude-haiku-4-5 for two different
        // models at two different rates.
        line.put("resolvedModelId", resp.resolvedModelId());
        line.put("success", resp.success());
        line.put("inputTokens", resp.inputTokens());
        line.put("outputTokens", resp.outputTokens());
        line.put("CostUsd", costUsdE5 / E5);
        line.put("CostUsdE5", costUsdE5);
        line.put("LatencyMs", resp.latencyMs());

        try {
            return mapper.writeValueAsString(line);
        } catch (JsonProcessingException ex) {
            // Unchecked: a caller cannot do anything useful about a serialisation failure of a
            // map of strings and longs, and making emit() throw a checked exception would push
            // a try/catch into every call site of the LLM path for a case that cannot happen.
            throw new UncheckedIOException("failed to render cost log line", ex);
        }
    }

    /** Exact USD for a cost in integer minor units, for callers that need a decimal. */
    public static BigDecimal toUsd(long costUsdE5) {
        return BigDecimal.valueOf(costUsdE5, CostResponseHeader.COST_SCALE);
    }
}
