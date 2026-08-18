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
 */
@Component
public class TaxpayerUpdatedListener {

    private static final Logger LOG = LoggerFactory.getLogger(TaxpayerUpdatedListener.class);

    private final TaxpayerReadModelRepository readModelRepository;

    public TaxpayerUpdatedListener(TaxpayerReadModelRepository readModelRepository) {
        this.readModelRepository = Objects.requireNonNull(readModelRepository, "readModelRepository must not be null");
    }

    @KafkaListener(topics = OutboxTopics.TAXPAYER_EVENTS, groupId = "taxcalc-read-model-builder",
            containerFactory = "kafkaListenerContainerFactory")
    public void onEvent(TaxpayerUpdatedEvent event) {
        TaxpayerReadModel document = readModelRepository.findById(event.aggregateId())
                .orElseGet(() -> new TaxpayerReadModel(event.aggregateId(), event.displayName(), event.filingStatus(),
                        event.homeJurisdiction(), event.createdAt(), new ArrayList<>()));
        document.applyEvent(event.displayName(), event.filingStatus(), event.homeJurisdiction(), event.createdAt());
        readModelRepository.save(document);
        LOG.info("consumed TaxpayerUpdated aggregateId={}", event.aggregateId());
    }
}
