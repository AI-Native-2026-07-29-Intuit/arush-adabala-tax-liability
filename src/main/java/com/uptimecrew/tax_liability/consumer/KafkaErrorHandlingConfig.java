package com.uptimecrew.tax_liability.consumer;

import com.uptimecrew.tax_liability.outbox.OutboxTopics;

import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.serialization.ByteArraySerializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.annotation.EnableKafka;
import org.springframework.kafka.config.ConcurrentKafkaListenerContainerFactory;
import org.springframework.kafka.core.ConsumerFactory;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.core.ProducerFactory;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.FixedBackOff;

/**
 * Wires poison-pill handling for {@link TaxpayerUpdatedListener} (W3 D3): a payload the listener
 * cannot process (malformed JSON, a domain error) is retried 3 times, 1 second apart
 * ({@link FixedBackOff}), then routed by {@link DeadLetterPublishingRecoverer} to
 * {@link OutboxTopics#TAXPAYER_EVENTS_DLT} instead of blocking the consumer group indefinitely.
 * {@code application.yml} pairs this with {@code ErrorHandlingDeserializer} on the consumer's
 * key/value deserializers, so a deserialization failure surfaces here too rather than crashing
 * the poll loop.
 *
 * <p>Also declares the app's producer {@link KafkaTemplate} explicitly (concretely typed as
 * {@code <String, String>}) rather than relying on Spring Boot's auto-configured default, which
 * is declared with wildcard generics ({@code KafkaTemplate<?, ?>}). That wildcard bean resolves
 * fine as the sole {@code KafkaTemplate} candidate, but once {@link #deadLetterKafkaTemplate}
 * exists too, Spring's generic-aware autowiring can no longer disambiguate a wildcard bean
 * against a concretely-typed injection point and autowiring fails - declaring both templates with
 * concrete generics sidesteps that ambiguity entirely. Both reuse Spring Boot's auto-configured
 * {@link ProducerFactory}'s connection properties (rather than re-reading {@code application.yml}
 * directly) so they still pick up the real broker address Testcontainers' {@code @ServiceConnection}
 * substitutes in tests, only overriding the (de)serializers each one needs.
 */
@Configuration
@EnableKafka
public class KafkaErrorHandlingConfig {

    @Bean
    public KafkaTemplate<String, String> kafkaTemplate(ProducerFactory<Object, Object> producerFactory) {
        DefaultKafkaProducerFactory<String, String> stringProducerFactory = new DefaultKafkaProducerFactory<>(
                producerFactory.getConfigurationProperties(), new StringSerializer(), new StringSerializer());
        return new KafkaTemplate<>(stringProducerFactory);
    }

    // (1) Only the VALUE deserializer is wrapped with ErrorHandlingDeserializer - the key
    // deserializer is a plain StringDeserializer, which never fails. So when recovery
    // republishes a record whose value failed to deserialize, it republishes the ORIGINAL raw
    // bytes for the value (Spring Kafka pulls them back out of the DeserializationException),
    // while the key is already a normal, successfully-deserialized String. A template using the
    // app's default StringSerializer for both would ClassCastException trying to serialize those
    // raw bytes as a String - this dedicated template pairs StringSerializer (key) with
    // ByteArraySerializer (value) to match what recovery actually republishes.
    @Bean
    public KafkaTemplate<String, byte[]> deadLetterKafkaTemplate(ProducerFactory<Object, Object> producerFactory) {
        DefaultKafkaProducerFactory<String, byte[]> byteArrayProducerFactory = new DefaultKafkaProducerFactory<>(
                producerFactory.getConfigurationProperties(), new StringSerializer(), new ByteArraySerializer());
        return new KafkaTemplate<>(byteArrayProducerFactory);
    }

    @Bean
    public DefaultErrorHandler kafkaErrorHandler(
            @Qualifier("deadLetterKafkaTemplate") KafkaTemplate<String, byte[]> deadLetterKafkaTemplate) {
        DeadLetterPublishingRecoverer recoverer = new DeadLetterPublishingRecoverer(deadLetterKafkaTemplate,
                (record, ex) -> new TopicPartition(OutboxTopics.TAXPAYER_EVENTS_DLT, record.partition()));
        return new DefaultErrorHandler(recoverer, new FixedBackOff(1000L, 3));
    }

    @Bean
    public ConcurrentKafkaListenerContainerFactory<String, TaxpayerUpdatedEvent> kafkaListenerContainerFactory(
            ConsumerFactory<String, TaxpayerUpdatedEvent> consumerFactory, DefaultErrorHandler kafkaErrorHandler) {
        ConcurrentKafkaListenerContainerFactory<String, TaxpayerUpdatedEvent> factory =
                new ConcurrentKafkaListenerContainerFactory<>();
        factory.setConsumerFactory(consumerFactory);
        factory.setCommonErrorHandler(kafkaErrorHandler);
        return factory;
    }
}
