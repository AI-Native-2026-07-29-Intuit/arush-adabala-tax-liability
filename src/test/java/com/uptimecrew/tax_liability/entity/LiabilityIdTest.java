package com.uptimecrew.tax_liability.entity;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class LiabilityIdTest {

    @Test
    void constructs_and_exposes_every_field() {
        LiabilityId id = new LiabilityId("tp-001", 2026);

        assertThat(id.getTaxpayerId()).isEqualTo("tp-001");
        assertThat(id.getTaxYear()).isEqualTo(2026);
        assertThat(id.toString()).contains("tp-001", "2026");
    }

    @Test
    void rejects_null_fields() {
        assertThatThrownBy(() -> new LiabilityId(null, 2026)).isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new LiabilityId("tp-001", null)).isInstanceOf(NullPointerException.class);
    }

    @Test
    void rejects_blank_taxpayer_id() {
        assertThatThrownBy(() -> new LiabilityId(" ", 2026)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void equality_is_based_on_both_fields() {
        LiabilityId a = new LiabilityId("tp-001", 2026);
        LiabilityId same = new LiabilityId("tp-001", 2026);
        LiabilityId differentYear = new LiabilityId("tp-001", 2027);
        LiabilityId differentTaxpayer = new LiabilityId("tp-002", 2026);

        assertThat(a).isEqualTo(same).hasSameHashCodeAs(same);
        assertThat(a).isNotEqualTo(differentYear);
        assertThat(a).isNotEqualTo(differentTaxpayer);
        assertThat(a).isNotEqualTo(null);
        assertThat(a).isNotEqualTo("tp-001");
    }
}
