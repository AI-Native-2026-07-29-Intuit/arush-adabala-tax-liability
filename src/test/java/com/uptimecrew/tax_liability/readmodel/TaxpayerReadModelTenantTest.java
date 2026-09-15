package com.uptimecrew.tax_liability.readmodel;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.time.Instant;
import java.util.List;

import org.junit.jupiter.api.Test;

/**
 * The {@code tenantId} invariants (W7 D1): the field the Python sidecar's {@code Taxpayer}
 * boundary model requires and validates the prefix of. These are unit tests on the document
 * itself - the repository round-trip lives in {@link TaxpayerReadModelRepositoryIT}.
 */
class TaxpayerReadModelTenantTest {

    private static final Instant CREATED_AT = Instant.parse("2026-01-15T12:00:00Z");

    private static TaxpayerReadModel withTenant(String tenantId) {
        return new TaxpayerReadModel("taxpayer-001", "Ada Lovelace", "SINGLE", "CALIFORNIA",
                CREATED_AT, List.of(), List.of(), tenantId);
    }

    @Test
    void tenantIdDefaultsToTheSharedTenantOnTheOlderConstructors() {
        TaxpayerReadModel document = new TaxpayerReadModel("taxpayer-001", "Ada Lovelace", "SINGLE",
                "CALIFORNIA", CREATED_AT, List.of());

        assertEquals(TaxpayerReadModel.DEFAULT_TENANT_ID, document.getTenantId());
    }

    @Test
    void tenantIdIsRetainedAsGiven() {
        assertEquals("tenant-acme", withTenant("tenant-acme").getTenantId());
    }

    @Test
    void anUnprefixedTenantIdIsRejected() {
        // The prefix is the contract the Python sidecar validates on the other side of the wire;
        // accepting a bare "acme" here would push the failure to that boundary instead.
        IllegalArgumentException thrown = assertThrows(IllegalArgumentException.class, () -> withTenant("acme"));

        assertEquals("tenantId must start with tenant-", thrown.getMessage());
    }

    @Test
    void aNullTenantIdIsRejected() {
        assertThrows(NullPointerException.class, () -> withTenant(null));
    }

    @Test
    void applyEventDoesNotClobberTheTenant() {
        // At-least-once Kafka redelivery re-projects an existing document in place. If applyEvent
        // touched tenantId, a redelivery would downgrade a real tenant to the default, because
        // TaxpayerUpdatedEvent does not carry one.
        TaxpayerReadModel document = withTenant("tenant-acme");

        document.applyEvent("Grace Hopper", "MARRIED_JOINT", "FEDERAL", CREATED_AT);

        assertEquals("tenant-acme", document.getTenantId());
    }
}
