package com.uptimecrew.tax_liability.config;

import java.util.List;

import com.uptimecrew.tax_liability.model.TaxBracket;
import com.uptimecrew.tax_liability.service.BracketResolver;
import com.uptimecrew.tax_liability.service.FlatStateBracketResolver;
import com.uptimecrew.tax_liability.service.NoIncomeTaxStateBracketResolver;
import com.uptimecrew.tax_liability.service.ProgressiveStateBracketResolver;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(BracketResolverProperties.class)
public class BracketResolverConfig {

    @Bean
    public BracketResolver flatStateBracketResolver(BracketResolverProperties properties) {
        BracketResolverProperties.FlatState flatState = properties.flatState();
        return new FlatStateBracketResolver(flatState.jurisdiction(), flatState.rate());
    }

    @Bean
    public BracketResolver noIncomeTaxStateBracketResolver(BracketResolverProperties properties) {
        BracketResolverProperties.NoIncomeTaxState noIncomeTaxState = properties.noIncomeTaxState();
        return new NoIncomeTaxStateBracketResolver(noIncomeTaxState.jurisdiction());
    }

    @Bean
    public BracketResolver progressiveStateBracketResolver(BracketResolverProperties properties) {
        BracketResolverProperties.ProgressiveState progressiveState = properties.progressiveState();
        List<TaxBracket> brackets = progressiveState.brackets().stream()
                .map(bracket -> new TaxBracket(
                        bracket.id(), progressiveState.jurisdiction(), bracket.rate(), bracket.floor(), bracket.ceiling()))
                .toList();
        return new ProgressiveStateBracketResolver(progressiveState.jurisdiction(), brackets);
    }
}
