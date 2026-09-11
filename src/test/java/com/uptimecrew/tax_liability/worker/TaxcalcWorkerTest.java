package com.uptimecrew.tax_liability.worker;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * {@link TaxcalcWorker}'s start-up assertion.
 *
 * <p>The behaviour under test is small but the failure it prevents is not: a worker whose
 * read-model listener is switched off still boots, still joins the consumer group and still looks
 * healthy, while KEDA scales the Deployment to {@code maxReplicaCount} on lag nobody is draining.
 * These two tests are what stop that from being a silent configuration mistake.
 */
class TaxcalcWorkerTest {

    @Test
    @DisplayName("a worker whose listener is enabled starts and reports the group it drains")
    void ready_when_listener_enabled() {
        TaxcalcWorker worker = new TaxcalcWorker(true);

        assertDoesNotThrow(worker::assertWorkerCanConsume);
        assertThat(worker.isListenerEnabled()).isTrue();
    }

    @Test
    @DisplayName("a worker whose listener is disabled refuses to start, naming the scaling consequence")
    void refuses_to_start_when_listener_disabled() {
        TaxcalcWorker worker = new TaxcalcWorker(false);

        // The message is asserted, not just the type. This exception is read off `kubectl logs`
        // on a crash-looping pod by someone who does not have this class open, so it has to say
        // what is wrong AND where the flag belongs - an IllegalStateException with a bare
        // "listener disabled" would send them to the listener rather than to the api Deployment
        // whose env block was copy-pasted.
        assertThatThrownBy(worker::assertWorkerCanConsume)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("taxcalc.read-model.listener.enabled=false")
                .hasMessageContaining("taxcalc-read-model-builder")
                .hasMessageContaining("maxReplicaCount")
                .hasMessageContaining("api Deployment");
    }

    @Test
    @DisplayName("toString carries the flag, so a context dump shows which mode the pod is in")
    void to_string_carries_flag() {
        assertThat(new TaxcalcWorker(true).toString()).contains("listenerEnabled=true");
        assertThat(new TaxcalcWorker(false).toString()).contains("listenerEnabled=false");
    }
}
