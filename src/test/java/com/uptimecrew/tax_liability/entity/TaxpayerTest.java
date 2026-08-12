package com.uptimecrew.tax_liability.entity;

import java.time.Instant;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class TaxpayerTest {

    private static final Instant NOW = Instant.parse("2026-01-01T00:00:00Z");

    @Test
    void constructs_and_exposes_every_field() {
        Taxpayer taxpayer = new Taxpayer("tp-001", "Ada Lovelace", "SINGLE", "FEDERAL", NOW);

        assertThat(taxpayer.getId()).isEqualTo("tp-001");
        assertThat(taxpayer.getDisplayName()).isEqualTo("Ada Lovelace");
        assertThat(taxpayer.getFilingStatus()).isEqualTo("SINGLE");
        assertThat(taxpayer.getHomeJurisdiction()).isEqualTo("FEDERAL");
        assertThat(taxpayer.getCreatedAt()).isEqualTo(NOW);
        assertThat(taxpayer.getLiabilities()).isEmpty();
        assertThat(taxpayer.toString()).contains("tp-001", "Ada Lovelace", "SINGLE", "FEDERAL");
    }

    @Test
    void rejects_null_fields() {
        assertThatThrownBy(() -> new Taxpayer(null, "Ada Lovelace", "SINGLE", "FEDERAL", NOW))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Taxpayer("tp-001", null, "SINGLE", "FEDERAL", NOW))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Taxpayer("tp-001", "Ada Lovelace", null, "FEDERAL", NOW))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Taxpayer("tp-001", "Ada Lovelace", "SINGLE", null, NOW))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Taxpayer("tp-001", "Ada Lovelace", "SINGLE", "FEDERAL", null))
                .isInstanceOf(NullPointerException.class);
    }

    @Test
    void rejects_blank_fields() {
        assertThatThrownBy(() -> new Taxpayer(" ", "Ada Lovelace", "SINGLE", "FEDERAL", NOW))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new Taxpayer("tp-001", " ", "SINGLE", "FEDERAL", NOW))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new Taxpayer("tp-001", "Ada Lovelace", " ", "FEDERAL", NOW))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new Taxpayer("tp-001", "Ada Lovelace", "SINGLE", " ", NOW))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void equality_is_based_on_id_only() {
        Taxpayer a = new Taxpayer("tp-001", "Ada Lovelace", "SINGLE", "FEDERAL", NOW);
        Taxpayer sameId = new Taxpayer("tp-001", "Different Name", "MARRIED_FILING_JOINTLY", "TEXAS", NOW);
        Taxpayer differentId = new Taxpayer("tp-002", "Ada Lovelace", "SINGLE", "FEDERAL", NOW);

        assertThat(a).isEqualTo(sameId).hasSameHashCodeAs(sameId);
        assertThat(a).isNotEqualTo(differentId);
        assertThat(a).isNotEqualTo(null);
        assertThat(a).isNotEqualTo("tp-001");
    }
}
