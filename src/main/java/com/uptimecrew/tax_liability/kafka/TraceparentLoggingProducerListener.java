package com.uptimecrew.tax_liability.kafka;

import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.clients.producer.RecordMetadata;
import org.apache.kafka.common.header.Header;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.support.ProducerListener;
import org.springframework.stereotype.Component;

/**
 * A tiny {@link ProducerListener} that logs the {@code traceparent} header on every send so
 * trace-context propagation can be eyeballed in the {@code bootRun} log without opening Jaeger
 * (W3 D5 Task 2). The {@code opentelemetry-spring-kafka-2.7} instrumentation installs a
 * {@code ProducerInterceptor} under the hood that injects the W3C {@code traceparent} header
 * before this listener fires - so if the header is missing here, either the instrumentation is
 * not on the classpath or {@code otel.instrumentation.spring-kafka.enabled} is false.
 */
@Component
public class TraceparentLoggingProducerListener implements ProducerListener<String, String> {

    private static final Logger LOG = LoggerFactory.getLogger(TraceparentLoggingProducerListener.class);

    @Override
    public void onSuccess(ProducerRecord<String, String> record, RecordMetadata recordMetadata) {
        Header header = record.headers().lastHeader("traceparent");
        if (header == null) {
            LOG.warn("outgoing kafka record has NO traceparent header topic={} key={}",
                    record.topic(), record.key());
            return;
        }
        LOG.info("outgoing traceparent={} topic={} key={}",
                new String(header.value()), record.topic(), record.key());
    }
}
