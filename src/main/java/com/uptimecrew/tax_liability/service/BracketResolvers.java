package com.uptimecrew.tax_liability.service;

import java.math.BigDecimal;

/**
 * Static factory for the {@link BracketResolver} strategy implementations. Not
 * instantiable — every variant is reached through one of the factory methods below,
 * each of which returns the interface type so callers cannot couple to a concrete class.
 */
public final class BracketResolvers {

    private BracketResolvers() {
        throw new AssertionError("BracketResolvers is not instantiable");
    }

    /**
     * @return a resolver for the federal bracket schedule
     */
    public static BracketResolver federal() {
        return new FederalBracketResolver();
    }

    /**
     * @return a resolver for a representative flat-rate state, where all income is
     *         taxed at one fixed rate
     */
    public static BracketResolver flatRateState() {
        return new FlatStateBracketResolver("COLORADO", new BigDecimal("0.044"));
    }

    /**
     * @return a resolver for a representative state that levies no income tax
     */
    public static BracketResolver noIncomeTaxState() {
        return new NoIncomeTaxStateBracketResolver("TEXAS");
    }
}
