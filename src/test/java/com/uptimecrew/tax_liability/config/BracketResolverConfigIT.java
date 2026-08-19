package com.uptimecrew.tax_liability.config;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;

import com.uptimecrew.tax_liability.AbstractPostgresIT;
import com.uptimecrew.tax_liability.service.BracketResolver;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;

/**
 * Proves {@link BracketResolverConfig}'s three beans actually resolve to the jurisdiction/rate/
 * bracket values declared under {@code taxcalc.strategies} in {@code application.yml} - not just
 * that the beans exist and the context doesn't crash.
 */
@SpringBootTest
@ActiveProfiles("test")
class BracketResolverConfigIT extends AbstractPostgresIT {

    private final BracketResolver flatStateBracketResolver;
    private final BracketResolver noIncomeTaxStateBracketResolver;
    private final BracketResolver progressiveStateBracketResolver;

    BracketResolverConfigIT(
            @Qualifier("flatStateBracketResolver") BracketResolver flatStateBracketResolver,
            @Qualifier("noIncomeTaxStateBracketResolver") BracketResolver noIncomeTaxStateBracketResolver,
            @Qualifier("progressiveStateBracketResolver") BracketResolver progressiveStateBracketResolver) {
        this.flatStateBracketResolver = flatStateBracketResolver;
        this.noIncomeTaxStateBracketResolver = noIncomeTaxStateBracketResolver;
        this.progressiveStateBracketResolver = progressiveStateBracketResolver;
    }

    @Test
    void flatStateBracketResolver_isBoundFromApplicationYml() {
        assertThat(flatStateBracketResolver.resolve(new BigDecimal("50000.00")))
                .get()
                .satisfies(bracket -> {
                    assertThat(bracket.jurisdiction()).isEqualTo("COLORADO");
                    assertThat(bracket.rate()).isEqualByComparingTo("0.044");
                });
    }

    @Test
    void noIncomeTaxStateBracketResolver_isBoundFromApplicationYml() {
        assertThat(noIncomeTaxStateBracketResolver.resolve(new BigDecimal("50000.00")))
                .get()
                .satisfies(bracket -> {
                    assertThat(bracket.jurisdiction()).isEqualTo("TEXAS");
                    assertThat(bracket.rate()).isEqualByComparingTo("0.00");
                });
    }

    @Test
    void progressiveStateBracketResolver_resolvesEachConfiguredBracketByFloor() {
        assertThat(progressiveStateBracketResolver.resolve(new BigDecimal("5000.00")))
                .get()
                .satisfies(bracket -> assertThat(bracket.id()).isEqualTo("ca-bracket-1"));

        assertThat(progressiveStateBracketResolver.resolve(new BigDecimal("15000.00")))
                .get()
                .satisfies(bracket -> assertThat(bracket.id()).isEqualTo("ca-bracket-2"));

        assertThat(progressiveStateBracketResolver.resolve(new BigDecimal("30000.00")))
                .get()
                .satisfies(bracket -> {
                    assertThat(bracket.id()).isEqualTo("ca-bracket-3");
                    assertThat(bracket.ceiling()).isNull();
                });
    }
}
