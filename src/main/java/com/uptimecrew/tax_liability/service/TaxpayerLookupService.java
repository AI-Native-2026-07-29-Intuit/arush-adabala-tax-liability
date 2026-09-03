package com.uptimecrew.tax_liability.service;

import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import io.opentelemetry.instrumentation.annotations.WithSpan;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * The instrumented read path (W5 D5): a thin seam in front of {@link TaxLiabilityService#findById}
 * that owns this deliverable's application-level meters, its lookup log line, and the manual
 * child span the trace view shows under the servlet span.
 *
 * <p><strong>Why this is a separate bean rather than instrumentation added to
 * {@link TaxLiabilityService} itself.</strong> {@code findById} is {@code @Cacheable} against
 * Redis, so Spring's cache proxy returns a hit <em>without ever entering the method body</em>.
 * Meters incremented inside that body would therefore count cache misses while claiming - by
 * name - to count lookups, and the RED dashboard would quietly under-report every cached read.
 * Instrumenting from outside the proxy counts what actually happened: one increment per request
 * that reached the service, cached or not.
 *
 * <p><strong>Why the meters are pre-built {@code private final} fields.</strong> Calling
 * {@code Counter.builder(...).register(registry)} per request is not free: each call allocates a
 * fresh {@code Meter.Id}, hashes it, and walks the registry's concurrent map before handing back
 * the same counter it returned last time. Building each meter once in the constructor reduces the
 * hot path to a single {@code increment()} on an already-resolved instance.
 *
 * <p><strong>Why the tags are what they are.</strong> {@code outcome} is a three-value enum
 * ({@code success} / {@code not_found} / {@code error}) and {@code taxpayer_type} is the filing
 * status, a small closed set. Neither the taxpayer id nor the caller's subject appears on any
 * meter: every distinct label value is a separate time series in Prometheus for the life of the
 * retention window, so an identifier as a label is an unbounded series count. Those identifiers
 * go into the log line and the span instead - see {@code manifests/observability/LABELS.md}.
 */
// non-final, against this repo's default: outside the cluster the @WithSpan below is honoured by
// the OpenTelemetry Spring Boot starter's AOP aspect, and Spring needs to CGLIB-subclass this
// bean to apply it - a final class fails context startup outright with
// "Cannot subclass final class ...". (Inside the cluster the Java agent rewrites the bytecode
// instead and needs no proxy, but the class has to work under both.) Same reason
// TaxLiabilityService is non-final for @Transactional.
@Service
public class TaxpayerLookupService {

    /** Emitted on every lookup attempt; the RED dashboard's rate and error panels derive from it. */
    static final String LOOKUPS_METER = "taxcalc.taxpayer.lookups";

    /** Business event counter: one increment per successfully served taxpayer read. */
    static final String RECOMPUTED_METER = "taxcalc.liability.recomputed";

    /** Wall-clock duration of the read path, including the Redis/Mongo/Postgres fallthrough. */
    static final String LOOKUP_TIMER = "taxcalc.taxpayer.lookup";

    /** Tag value used wherever the read model carries no filing status to report. */
    static final String UNKNOWN_TAXPAYER_TYPE = "unknown";

    private static final Logger LOG = LoggerFactory.getLogger(TaxpayerLookupService.class);

    private final TaxLiabilityService delegate;

    // Pre-built meters - one instance per JVM, reused on every call (see the class javadoc).
    private final Counter lookupsAttempted;
    private final Counter lookupsNotFound;
    private final Counter lookupsFailed;
    private final Timer lookupTimer;
    private final MeterRegistry registry;

    // The business counter's tag set is only known at call time, so its meters are memoised here
    // rather than resolved through Micrometer's builder on every request - see recomputedCounter.
    // Bounded by construction: filing statuses (plus `unknown`) times three outcomes.
    private final ConcurrentMap<String, Counter> recomputedCounters = new ConcurrentHashMap<>();

    /**
     * @param delegate the cached read path this service instruments, must not be null
     * @param registry the Micrometer registry every meter below is bound to, must not be null
     */
    public TaxpayerLookupService(TaxLiabilityService delegate, MeterRegistry registry) {
        this.delegate = Objects.requireNonNull(delegate, "delegate must not be null");
        this.registry = Objects.requireNonNull(registry, "registry must not be null");

        this.lookupsAttempted = Counter.builder(LOOKUPS_METER)
                .description("Taxpayer read-model lookups attempted")
                .tag("outcome", "attempted")
                .register(registry);
        this.lookupsNotFound = Counter.builder(LOOKUPS_METER)
                .description("Taxpayer read-model lookups that found no taxpayer")
                .tag("outcome", "not_found")
                .register(registry);
        this.lookupsFailed = Counter.builder(LOOKUPS_METER)
                .description("Taxpayer read-model lookups that threw")
                .tag("outcome", "error")
                .register(registry);
        this.lookupTimer = Timer.builder(LOOKUP_TIMER)
                .description("Duration of the taxpayer read-model lookup")
                // Publishes the bucketed _bucket series, so a latency panel can be built from
                // this business timer and not only from Boot's http_server_requests family.
                .publishPercentileHistogram()
                .register(registry);
    }

    /**
     * Looks a taxpayer up through the cached read path, recording one attempt, one outcome and
     * one duration sample per call.
     *
     * <p>Annotated {@link WithSpan} so the trace carries a named child span
     * ({@code TaxpayerLookupService.findById}) under the servlet span the OpenTelemetry agent
     * creates - the entry span alone says a request took 800ms, this one says which part of it
     * did. The annotation is honoured by the agent's bytecode instrumentation in the cluster and
     * by the OpenTelemetry Spring Boot starter's aspect everywhere else; because the call comes
     * from another bean ({@code TaxpayerController}) rather than from inside this class, it works
     * under both mechanisms.
     *
     * @param id the taxpayer's id, must not be null
     * @return the taxpayer's read model, or an empty {@link Optional} if no taxpayer has that id
     * @throws RuntimeException whatever the delegate throws, after recording the {@code error}
     *         outcome - the exception is deliberately not swallowed, so the controller's error
     *         handling and the HTTP 5xx metric still see it
     */
    @WithSpan
    public Optional<TaxpayerReadModel> findById(String id) {
        Objects.requireNonNull(id, "id must not be null");
        lookupsAttempted.increment();

        // The correlation id is already in MDC (CorrelationIdFilter), and the OTel agent's
        // logback instrumentation adds trace_id/span_id there too, so this one line carries all
        // three identifiers once LogstashEncoder serialises the MDC into the JSON envelope.
        LOG.info("lookup attempted taxpayerId={}", id);

        Timer.Sample sample = Timer.start(registry);
        try {
            Optional<TaxpayerReadModel> found = delegate.findById(id);
            if (found.isEmpty()) {
                lookupsNotFound.increment();
                recomputedCounter(UNKNOWN_TAXPAYER_TYPE, "not_found").increment();
                LOG.info("lookup result=not_found taxpayerId={}", id);
            } else {
                recomputedCounter(taxpayerType(found.get()), "success").increment();
                LOG.info("lookup result=found taxpayerId={}", id);
            }
            return found;
        } catch (RuntimeException ex) {
            lookupsFailed.increment();
            recomputedCounter(UNKNOWN_TAXPAYER_TYPE, "error").increment();
            LOG.warn("lookup result=error taxpayerId={}: {}", id, ex.getMessage(), ex);
            throw ex;
        } finally {
            sample.stop(lookupTimer);
        }
    }

    /**
     * The one custom business metric outside the {@code http_server_requests} family. A
     * {@link Counter}, not a gauge: it counts events ("we served another liability read"), not
     * state, so it never has to survive a pod restart - {@code rate()} over a monotonic counter
     * handles the reset for free.
     *
     * <p>Its {@code taxpayer_type} tag is not known until the read model comes back, so this one
     * cannot be a constructor-built field like the meters above. It is still never resolved
     * through the builder on the hot path: {@code Counter.builder(...).register(registry)}
     * allocates a fresh {@code Meter.Id}, applies every registered {@code MeterFilter} and hashes
     * its way through the registry's map on <em>every</em> call, even when handing back a meter it
     * already returned a thousand times. The {@link ConcurrentHashMap} in front of it turns the
     * steady state into one hash lookup on a small, bounded key set (filing statuses plus
     * {@code unknown}, times three outcomes), and the builder runs once per distinct combination.
     *
     * @param taxpayerType the filing status this read resolved to, or {@code unknown}
     * @param outcome one of {@code success}, {@code not_found}, {@code error}
     * @return the registered counter for that tag pair
     */
    private Counter recomputedCounter(String taxpayerType, String outcome) {
        return recomputedCounters.computeIfAbsent(taxpayerType + '|' + outcome, key ->
                Counter.builder(RECOMPUTED_METER)
                        .description("Taxpayer liability reads served, by filing status and outcome")
                        .tag("taxpayer_type", taxpayerType)
                        .tag("outcome", outcome)
                        .register(registry));
    }

    /**
     * @param model the read model just returned, must not be null
     * @return the model's filing status, or {@code unknown} when it carries none - never null,
     *         because Micrometer rejects a null tag value outright
     */
    private static String taxpayerType(TaxpayerReadModel model) {
        String filingStatus = model.getFilingStatus();
        return (filingStatus == null || filingStatus.isBlank()) ? UNKNOWN_TAXPAYER_TYPE : filingStatus;
    }
}
