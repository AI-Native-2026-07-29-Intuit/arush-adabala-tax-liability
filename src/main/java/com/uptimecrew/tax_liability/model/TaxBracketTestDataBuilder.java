package com.uptimecrew.tax_liability.model;

import java.math.BigDecimal;

/**
 * Fluent test data builder for {@link TaxBracket}. Every field defaults to a value that
 * passes the record's compact-constructor validation, so a caller only overrides the
 * fields relevant to the scenario under test.
 */
public final class TaxBracketTestDataBuilder {

    private String id = "tax-bracket-synth-001";
    private String jurisdiction = "CALIFORNIA";
    private BigDecimal rate = new BigDecimal("0.22");
    private BigDecimal floor = new BigDecimal("50000.00");
    private BigDecimal ceiling = new BigDecimal("100000.00");

    public TaxBracketTestDataBuilder withId(String value) {
        this.id = value;
        return this;
    }

    public TaxBracketTestDataBuilder withJurisdiction(String value) {
        this.jurisdiction = value;
        return this;
    }

    public TaxBracketTestDataBuilder withRate(BigDecimal value) {
        this.rate = value;
        return this;
    }

    public TaxBracketTestDataBuilder withFloor(BigDecimal value) {
        this.floor = value;
        return this;
    }

    public TaxBracketTestDataBuilder withCeiling(BigDecimal value) {
        this.ceiling = value;
        return this;
    }

    public TaxBracket build() {
        return new TaxBracket(id, jurisdiction, rate, floor, ceiling);
    }

    /** Factory: call as {@code TaxBracketTestDataBuilder.aTaxBracket().withX(...).build()}. */
    public static TaxBracketTestDataBuilder aTaxBracket() {
        return new TaxBracketTestDataBuilder();
    }
}
