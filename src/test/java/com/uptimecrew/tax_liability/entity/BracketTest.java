package com.uptimecrew.tax_liability.entity;

import java.math.BigDecimal;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Covers {@link Bracket}'s constructor validation and its identity-on-{@code id}-only {@code
 * equals}/{@code hashCode}.
 */
class BracketTest {

    @Test
    void constructs_and_exposes_every_field() {
        Bracket bracket = new Bracket("fed-bracket-10", "FEDERAL", 2026, "10pct",
                new BigDecimal("0.10"), new BigDecimal("0"), new BigDecimal("11600"));

        assertThat(bracket.getId()).isEqualTo("fed-bracket-10");
        assertThat(bracket.getJurisdiction()).isEqualTo("FEDERAL");
        assertThat(bracket.getTaxYear()).isEqualTo(2026);
        assertThat(bracket.getCode()).isEqualTo("10pct");
        assertThat(bracket.getRate()).isEqualByComparingTo("0.10");
        assertThat(bracket.getFloorAmount()).isEqualByComparingTo("0");
        assertThat(bracket.getCeilingAmount()).isEqualByComparingTo("11600");
        assertThat(bracket.toString()).contains("fed-bracket-10", "FEDERAL");
    }

    @Test
    void allows_a_null_ceiling_for_an_unbounded_top_bracket() {
        Bracket bracket = new Bracket("fed-bracket-24", "FEDERAL", 2026, "24pct",
                new BigDecimal("0.24"), new BigDecimal("100525"), null);

        assertThat(bracket.getCeilingAmount()).isNull();
    }

    @Test
    void rejects_null_fields() {
        assertThatThrownBy(() -> new Bracket(null, "FEDERAL", 2026, "10pct",
                new BigDecimal("0.10"), new BigDecimal("0"), null)).isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Bracket("id", null, 2026, "10pct",
                new BigDecimal("0.10"), new BigDecimal("0"), null)).isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Bracket("id", "FEDERAL", null, "10pct",
                new BigDecimal("0.10"), new BigDecimal("0"), null)).isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Bracket("id", "FEDERAL", 2026, null,
                new BigDecimal("0.10"), new BigDecimal("0"), null)).isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Bracket("id", "FEDERAL", 2026, "10pct",
                null, new BigDecimal("0"), null)).isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new Bracket("id", "FEDERAL", 2026, "10pct",
                new BigDecimal("0.10"), null, null)).isInstanceOf(NullPointerException.class);
    }

    @Test
    void rejects_blank_id_jurisdiction_and_code() {
        assertThatThrownBy(() -> new Bracket(" ", "FEDERAL", 2026, "10pct",
                new BigDecimal("0.10"), new BigDecimal("0"), null)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new Bracket("id", " ", 2026, "10pct",
                new BigDecimal("0.10"), new BigDecimal("0"), null)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new Bracket("id", "FEDERAL", 2026, " ",
                new BigDecimal("0.10"), new BigDecimal("0"), null)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void rejects_negative_rate_or_floor() {
        assertThatThrownBy(() -> new Bracket("id", "FEDERAL", 2026, "10pct",
                new BigDecimal("-0.10"), new BigDecimal("0"), null)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new Bracket("id", "FEDERAL", 2026, "10pct",
                new BigDecimal("0.10"), new BigDecimal("-1"), null)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void rejects_a_ceiling_at_or_below_the_floor() {
        assertThatThrownBy(() -> new Bracket("id", "FEDERAL", 2026, "10pct",
                new BigDecimal("0.10"), new BigDecimal("100"), new BigDecimal("100")))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new Bracket("id", "FEDERAL", 2026, "10pct",
                new BigDecimal("0.10"), new BigDecimal("100"), new BigDecimal("50")))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void equality_is_based_on_id_only() {
        Bracket a = new Bracket("id-1", "FEDERAL", 2026, "10pct", new BigDecimal("0.10"), new BigDecimal("0"), null);
        Bracket sameId = new Bracket("id-1", "TEXAS", 2025, "0pct", new BigDecimal("0"), new BigDecimal("0"), null);
        Bracket differentId = new Bracket("id-2", "FEDERAL", 2026, "10pct", new BigDecimal("0.10"), new BigDecimal("0"), null);

        assertThat(a).isEqualTo(sameId).hasSameHashCodeAs(sameId);
        assertThat(a).isNotEqualTo(differentId);
        assertThat(a).isNotEqualTo(null);
        assertThat(a).isNotEqualTo("id-1");
    }
}
