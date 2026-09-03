package com.uptimecrew.tax_liability.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

/**
 * Unit coverage for {@link TaxpayerLookupService}'s instrumentation contract: every branch of the
 * lookup (found, not found, thrown) increments the meters it claims to, tags them with a bounded
 * value, and leaves the delegate's own behaviour untouched.
 *
 * <p>Uses a real {@link SimpleMeterRegistry} rather than a mocked {@code MeterRegistry}: the thing
 * under test is what ends up registered - meter names, tag keys, tag values - and a mock would
 * only prove that the builder methods were called.
 */
@ExtendWith(MockitoExtension.class)
class TaxpayerLookupServiceTest {

    private static final String ID = "txp_synth_001";

    @Mock
    private TaxLiabilityService delegate;

    private MeterRegistry registry;
    private TaxpayerLookupService service;

    @BeforeEach
    void setUp() {
        registry = new SimpleMeterRegistry();
        service = new TaxpayerLookupService(delegate, registry);
    }

    @Test
    void findByIdReturnsTheDelegatesResultAndCountsASuccess() {
        TaxpayerReadModel model = readModel("SINGLE");
        when(delegate.findById(ID)).thenReturn(Optional.of(model));

        Optional<TaxpayerReadModel> found = service.findById(ID);

        assertThat(found).containsSame(model);
        assertThat(lookups("attempted")).isEqualTo(1.0);
        assertThat(recomputed("SINGLE", "success")).isEqualTo(1.0);
        assertThat(timer().count()).isEqualTo(1L);
    }

    @Test
    void findByIdCountsANotFoundOutcomeWhenTheDelegateReturnsEmpty() {
        when(delegate.findById(ID)).thenReturn(Optional.empty());

        assertThat(service.findById(ID)).isEmpty();

        assertThat(lookups("attempted")).isEqualTo(1.0);
        assertThat(lookups("not_found")).isEqualTo(1.0);
        // The not-found branch has no read model to read a filing status off, so the business
        // counter is tagged with the explicit `unknown` sentinel - never a null, which
        // Micrometer rejects outright.
        assertThat(recomputed(TaxpayerLookupService.UNKNOWN_TAXPAYER_TYPE, "not_found")).isEqualTo(1.0);
    }

    @Test
    void findByIdCountsAnErrorOutcomeAndRethrows() {
        when(delegate.findById(ID)).thenThrow(new IllegalStateException("mongo is down"));

        assertThatThrownBy(() -> service.findById(ID))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("mongo is down");

        assertThat(lookups("error")).isEqualTo(1.0);
        assertThat(recomputed(TaxpayerLookupService.UNKNOWN_TAXPAYER_TYPE, "error")).isEqualTo(1.0);
        // Recorded in a finally block, so a failed lookup still contributes its duration - an
        // error path that silently drops out of the latency histogram makes an outage look fast.
        assertThat(timer().count()).isEqualTo(1L);
    }

    @Test
    void findByIdTagsTheBusinessCounterWithUnknownWhenTheReadModelHasNoFilingStatus() {
        when(delegate.findById(ID)).thenReturn(Optional.of(readModel("   ")));

        service.findById(ID);

        assertThat(recomputed(TaxpayerLookupService.UNKNOWN_TAXPAYER_TYPE, "success")).isEqualTo(1.0);
    }

    @Test
    void repeatedLookupsReuseTheSameMeterInstances() {
        when(delegate.findById(anyString())).thenReturn(Optional.of(readModel("SINGLE")));

        service.findById(ID);
        service.findById("txp_synth_002");

        // Two calls, one counter: the meters are pre-built fields, and the per-call business
        // counter resolves back to the same registered instance for a repeated tag pair.
        assertThat(lookups("attempted")).isEqualTo(2.0);
        assertThat(registry.find(TaxpayerLookupService.RECOMPUTED_METER).counters()).hasSize(1);
        assertThat(recomputed("SINGLE", "success")).isEqualTo(2.0);
    }

    @Test
    void constructorRejectsNullCollaborators() {
        assertThatThrownBy(() -> new TaxpayerLookupService(null, registry))
                .isInstanceOf(NullPointerException.class)
                .hasMessage("delegate must not be null");
        assertThatThrownBy(() -> new TaxpayerLookupService(delegate, null))
                .isInstanceOf(NullPointerException.class)
                .hasMessage("registry must not be null");
    }

    @Test
    void findByIdRejectsANullId() {
        assertThatThrownBy(() -> service.findById(null))
                .isInstanceOf(NullPointerException.class)
                .hasMessage("id must not be null");
    }

    private double lookups(String outcome) {
        Counter counter = registry.find(TaxpayerLookupService.LOOKUPS_METER).tag("outcome", outcome).counter();
        return counter == null ? 0.0 : counter.count();
    }

    private double recomputed(String taxpayerType, String outcome) {
        Counter counter = registry.find(TaxpayerLookupService.RECOMPUTED_METER)
                .tag("taxpayer_type", taxpayerType)
                .tag("outcome", outcome)
                .counter();
        return counter == null ? 0.0 : counter.count();
    }

    private Timer timer() {
        return registry.get(TaxpayerLookupService.LOOKUP_TIMER).timer();
    }

    private static TaxpayerReadModel readModel(String filingStatus) {
        return new TaxpayerReadModel(ID, "Synthetic Taxpayer", filingStatus, "FEDERAL",
                Instant.parse("2026-01-01T00:00:00Z"), List.of());
    }
}
