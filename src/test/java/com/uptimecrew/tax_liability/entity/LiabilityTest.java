package com.uptimecrew.tax_liability.entity;

import java.math.BigDecimal;
import java.time.Instant;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class LiabilityTest {

    private static final Instant NOW = Instant.parse("2026-01-01T00:00:00Z");

    private static Taxpayer aTaxpayer() {
        return new Taxpayer("tp-001", "Ada Lovelace", "SINGLE", "FEDERAL", NOW);
    }

    private static Bracket aBracket() {
        return new Bracket("fed-bracket-10", "FEDERAL", 2026, "10pct",
                new BigDecimal("0.10"), new BigDecimal("0"), new BigDecimal("11600"));
    }

    @Test
    void constructs_and_exposes_every_field() {
        Taxpayer taxpayer = aTaxpayer();
        Bracket bracket = aBracket();
        Liability liability = new Liability(taxpayer, 2026, bracket, new BigDecimal("1000.00"), new BigDecimal("100.00"), NOW);

        assertThat(liability.getTaxpayerId()).isEqualTo("tp-001");
        assertThat(liability.getTaxYear()).isEqualTo(2026);
        assertThat(liability.getTaxpayer()).isEqualTo(taxpayer);
        assertThat(liability.getBracket()).isEqualTo(bracket);
        assertThat(liability.getTaxableAmount()).isEqualByComparingTo("1000.00");
        assertThat(liability.getLiabilityAmount()).isEqualByComparingTo("100.00");
        assertThat(liability.getComputedAt()).isEqualTo(NOW);
        assertThat(liability.toString()).contains("tp-001", "2026");
    }

    @Test
    void rejects_null_fields() {
        Taxpayer taxpayer = aTaxpayer();
        Bracket bracket = aBracket();

        assertThatThrownBy(() -> new Liability(null, 2026, bracket, new BigDecimal("1000.00"), new BigDecimal("100.00"), NOW))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Liability(taxpayer, null, bracket, new BigDecimal("1000.00"), new BigDecimal("100.00"), NOW))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Liability(taxpayer, 2026, null, new BigDecimal("1000.00"), new BigDecimal("100.00"), NOW))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Liability(taxpayer, 2026, bracket, null, new BigDecimal("100.00"), NOW))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Liability(taxpayer, 2026, bracket, new BigDecimal("1000.00"), null, NOW))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Liability(taxpayer, 2026, bracket, new BigDecimal("1000.00"), new BigDecimal("100.00"), null))
                .isInstanceOf(NullPointerException.class);
    }

    @Test
    void rejects_negative_amounts() {
        Taxpayer taxpayer = aTaxpayer();
        Bracket bracket = aBracket();

        assertThatThrownBy(() -> new Liability(taxpayer, 2026, bracket, new BigDecimal("-1.00"), new BigDecimal("100.00"), NOW))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new Liability(taxpayer, 2026, bracket, new BigDecimal("1000.00"), new BigDecimal("-1.00"), NOW))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void equality_is_based_on_taxpayer_id_and_tax_year() {
        Taxpayer taxpayer = aTaxpayer();
        Bracket bracket = aBracket();
        Liability a = new Liability(taxpayer, 2026, bracket, new BigDecimal("1000.00"), new BigDecimal("100.00"), NOW);
        Liability sameKey = new Liability(taxpayer, 2026, bracket, new BigDecimal("2000.00"), new BigDecimal("200.00"), NOW);
        Liability differentYear = new Liability(taxpayer, 2027, bracket, new BigDecimal("1000.00"), new BigDecimal("100.00"), NOW);

        assertThat(a).isEqualTo(sameKey).hasSameHashCodeAs(sameKey);
        assertThat(a).isNotEqualTo(differentYear);
        assertThat(a).isNotEqualTo(null);
        assertThat(a).isNotEqualTo("tp-001");
    }
}
