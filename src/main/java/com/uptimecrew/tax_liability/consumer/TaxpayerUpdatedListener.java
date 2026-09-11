package com.uptimecrew.tax_liability.consumer;

import java.util.ArrayList;
import java.util.Objects;

import com.uptimecrew.tax_liability.outbox.OutboxTopics;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Consumes {@link OutboxTopics#TAXPAYER_EVENTS} and re-projects the Mongo read model (W3 D3):
 * where W2 D5's write-through happens inline inside {@code computeLiability}'s transaction, this
 * listener rebuilds the same document asynchronously from the Kafka event instead, so the read
 * model stays current even for consumers that only see the event stream. Idempotent: applying
 * the same event twice produces the same document, so Kafka's at-least-once redelivery is safe.
 *
 * <h2>W6 D5: why start-up is property-gated</h2>
 *
 * <p>KEDA scales the {@code taxcalc-api-worker} Deployment on the consumer-group lag of
 * {@value #READ_MODEL_GROUP}. Lag is a property of the <em>group</em>, not of a Deployment - so
 * every process that joins that group drains the signal KEDA is scaling on, whoever it belongs
 * to.
 *
 * <p>Until W6 D5 that was harmless, because W5 D3 deployed no Kafka broker at all (only a bare
 * Service so the bootstrap hostname resolved). Task 1 deploys a real broker, and at that moment
 * the {@code taxcalc-api} pods - which run this same image and therefore this same listener -
 * start consuming {@value #READ_MODEL_GROUP} alongside the worker. Two or three api replicas
 * comfortably keep a dev-rate topic drained, so the lag KEDA polls sits near zero no matter how
 * much is produced, and the worker never leaves {@code minReplicaCount: 0}.
 *
 * <p>Nothing about that failure looks like a failure. The ScaledObject is {@code READY=True}, the
 * trigger is valid, the broker is reachable, the read model <em>is</em> being updated (by the api
 * pods), and the only symptom is that scale-to-zero never becomes scale-to-anything. It reads as
 * "KEDA isn't working" and sends you to the operator logs, which are clean.
 *
 * <p>So the api Deployment sets {@code taxcalc.read-model.listener.enabled=false} and the worker
 * leaves it at its default of {@code true}. The default is {@code true} deliberately: every
 * existing context - the {@code local}, {@code docker} and {@code test} profiles, and
 * {@code TaxpayerEventFlowIT}'s consumer assertions - depends on this listener running without
 * anyone opting in, and a default of {@code false} would silently turn those green tests into
 * tests of nothing.
 *
 * @see com.uptimecrew.tax_liability.worker.TaxcalcWorker TaxcalcWorker, which refuses to start if
 *      this flag is false in the worker run mode
 */
@Component
public class TaxpayerUpdatedListener {

    /**
     * The consumer group this projection joins - and therefore the group whose lag KEDA's
     * {@code kafka} trigger reads. Shared as a constant so the ScaledObject's
     * {@code consumerGroup}, this annotation and {@code TaxcalcWorker}'s start-up assertion
     * cannot drift into naming three different groups.
     */
    public static final String READ_MODEL_GROUP = "taxcalc-read-model-builder";

    private static final Logger LOG = LoggerFactory.getLogger(TaxpayerUpdatedListener.class);

    private final TaxpayerReadModelRepository readModelRepository;

    public TaxpayerUpdatedListener(TaxpayerReadModelRepository readModelRepository) {
        this.readModelRepository = Objects.requireNonNull(readModelRepository, "readModelRepository must not be null");
    }

    /**
     * Apply one event to the read model.
     *
     * <p>{@code autoStartup} is a SpEL-resolved property rather than a hard {@code "true"}: see
     * the class Javadoc for why the api run mode has to switch this off and the worker must not.
     * Note this controls whether the listener <em>container starts</em>, not whether the bean
     * exists - the bean is always registered, so JMX and the endpoint both still show it, and a
     * {@code false} here is only visible as a container in the {@code stopped} state.
     *
     * @param event the deserialized taxpayer event; never null
     */
    @KafkaListener(topics = OutboxTopics.TAXPAYER_EVENTS, groupId = READ_MODEL_GROUP,
            containerFactory = "kafkaListenerContainerFactory",
            autoStartup = "${taxcalc.read-model.listener.enabled:true}")
    public void onEvent(TaxpayerUpdatedEvent event) {
        TaxpayerReadModel document = readModelRepository.findById(event.aggregateId())
                .orElseGet(() -> new TaxpayerReadModel(event.aggregateId(), event.displayName(), event.filingStatus(),
                        event.homeJurisdiction(), event.createdAt(), new ArrayList<>()));
        document.applyEvent(event.displayName(), event.filingStatus(), event.homeJurisdiction(), event.createdAt());
        readModelRepository.save(document);
        LOG.info("consumed TaxpayerUpdated aggregateId={}", event.aggregateId());
    }
}
