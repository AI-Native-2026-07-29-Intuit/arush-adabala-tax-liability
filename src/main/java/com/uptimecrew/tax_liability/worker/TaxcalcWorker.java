package com.uptimecrew.tax_liability.worker;

import java.util.Objects;

import com.uptimecrew.tax_liability.outbox.OutboxTopics;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.annotation.Profile;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/**
 * The {@code worker} run mode (W6 D5 Task 1): the same image as {@code taxcalc-api}, started with
 * {@code SPRING_PROFILES_ACTIVE=k8s,worker}, serving no HTTP and doing nothing but consuming
 * {@link OutboxTopics#TAXPAYER_EVENTS} on the consumer group
 * {@value com.uptimecrew.tax_liability.consumer.TaxpayerUpdatedListener#READ_MODEL_GROUP} and
 * re-projecting the Mongo read model - exactly as the W3 D3 consumer does.
 *
 * <p>This class deliberately contains no consumption logic of its own. The projection already
 * exists in {@link com.uptimecrew.tax_liability.consumer.TaxpayerUpdatedListener}, and forking a
 * second copy of it for the worker would mean two implementations of the same read model that
 * drift silently - the api's copy and the worker's copy would disagree only for events whose
 * shape changed, which is precisely the case nobody tests. What the worker adds is a *run mode*,
 * not a second projection.
 *
 * <h2>Why this class exists at all, rather than just a profile</h2>
 *
 * <p>It exists to fail fast on one specific misconfiguration that is otherwise invisible and that
 * this deliverable's whole scaling story rests on.
 *
 * <p>KEDA scales this Deployment on the <em>consumer-group lag</em> of
 * {@value com.uptimecrew.tax_liability.consumer.TaxpayerUpdatedListener#READ_MODEL_GROUP}. A
 * worker pod that boots healthily but has its listener switched off still joins the group, still
 * gets partitions assigned, and still reports {@code Ready} to the kubelet - it simply never
 * commits an offset. Lag therefore never falls, so KEDA keeps scaling <em>up</em>, to
 * {@code maxReplicaCount}, and holds there. Every surface an operator would check looks correct:
 * the ScaledObject is {@code READY=True} and {@code ACTIVE=True}, the pods are {@code Running},
 * the HPA KEDA generates is at its ceiling "because there is work". The only symptom is a read
 * model that never updates and a bill for twenty pods doing nothing.
 *
 * <p>That misconfiguration is reachable because the listener's start-up is property-gated (see
 * {@link com.uptimecrew.tax_liability.consumer.TaxpayerUpdatedListener}): the api Deployment
 * sets the flag to {@code false} so that api pods do not drain the very lag the worker is scaled
 * on. One copy-pasted env block between the two Deployments is all it takes. Refusing to start is
 * the correct response - a worker that cannot work is not degraded, it is wrong, and
 * {@code CrashLoopBackOff} is a signal every operator already knows how to read.
 */
@Component
@Profile("worker")
public class TaxcalcWorker {

    private static final Logger LOG = LoggerFactory.getLogger(TaxcalcWorker.class);

    private final boolean listenerEnabled;

    /**
     * @param listenerEnabled the resolved value of the same property
     *                        {@code TaxpayerUpdatedListener} gates its {@code autoStartup} on.
     *                        Read here as a plain {@code boolean} rather than by inspecting the
     *                        listener container registry, because the registry answers "is a
     *                        container registered" - which is true either way - and not "was it
     *                        told to start".
     */
    public TaxcalcWorker(
            @Value("${taxcalc.read-model.listener.enabled:true}") boolean listenerEnabled) {
        this.listenerEnabled = listenerEnabled;
    }

    /**
     * Assert the worker can actually do its job, and say what it is bound to.
     *
     * <p>Bound to {@link ApplicationReadyEvent} rather than {@code @PostConstruct} so the failure
     * lands after the context is otherwise up. A {@code @PostConstruct} throw aborts the refresh
     * and buries the reason under a {@code BeanCreationException} stack; by ready-time the
     * exception propagates as the last thing in the log, which is where anyone reading
     * {@code kubectl logs} on a crash-looping pod looks first.
     *
     * @throws IllegalStateException if the read-model listener is disabled, which would make this
     *                               worker a pod that consumes nothing while KEDA scales it to
     *                               the ceiling on lag it is not draining
     */
    @EventListener(ApplicationReadyEvent.class)
    public void assertWorkerCanConsume() {
        if (!listenerEnabled) {
            throw new IllegalStateException(
                    "worker profile is active but taxcalc.read-model.listener.enabled=false: this "
                            + "pod would join consumer group "
                            + com.uptimecrew.tax_liability.consumer.TaxpayerUpdatedListener.READ_MODEL_GROUP
                            + " and never commit an offset, so KEDA would scale the Deployment to "
                            + "maxReplicaCount on lag nobody is draining. The flag belongs on the "
                            + "api Deployment, not this one.");
        }
        LOG.info("taxcalc worker ready: topic={} group={} (no HTTP server in this run mode)",
                OutboxTopics.TAXPAYER_EVENTS,
                com.uptimecrew.tax_liability.consumer.TaxpayerUpdatedListener.READ_MODEL_GROUP);
    }

    /**
     * Whether this worker's read-model listener was configured to start.
     *
     * @return true when the listener is enabled; a false here is what
     *         {@link #assertWorkerCanConsume()} refuses to start on
     */
    public boolean isListenerEnabled() {
        return listenerEnabled;
    }

    @Override
    public String toString() {
        return "TaxcalcWorker{listenerEnabled=" + listenerEnabled + "}";
    }

    @Override
    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof TaxcalcWorker that)) {
            return false;
        }
        return listenerEnabled == that.listenerEnabled;
    }

    @Override
    public int hashCode() {
        return Objects.hash(listenerEnabled);
    }
}
