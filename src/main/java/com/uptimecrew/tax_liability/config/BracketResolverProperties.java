package com.uptimecrew.tax_liability.config;

import java.math.BigDecimal;
import java.util.List;

import org.springframework.boot.context.properties.ConfigurationProperties;

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
