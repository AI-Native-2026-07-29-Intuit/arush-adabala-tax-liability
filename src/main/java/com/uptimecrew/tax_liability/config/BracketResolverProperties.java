package com.uptimecrew.tax_liability.config;

import java.math.BigDecimal;
import java.util.List;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Binds the {@code taxcalc.strategies} block of {@code application.yml} - the jurisdiction, rate,
 * and (for the progressive state) bracket table each of {@link BracketResolverConfig}'s three
 * beans is built from.
 */
@ConfigurationProperties(prefix = "taxcalc.strategies")
public record BracketResolverProperties(
        FlatState flatState,
        NoIncomeTaxState noIncomeTaxState,
        ProgressiveState progressiveState) {

    public record FlatState(String jurisdiction, BigDecimal rate) {
    }

    public record NoIncomeTaxState(String jurisdiction) {
    }

    public record ProgressiveState(String jurisdiction, List<Bracket> brackets) {
    }

    public record Bracket(String id, BigDecimal rate, BigDecimal floor, BigDecimal ceiling) {
    }
}
