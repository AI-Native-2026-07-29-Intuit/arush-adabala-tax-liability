package com.uptimecrew.tax_liability.model;

/**
 * The category an {@link IncomeEvent}'s income falls under.
 */
public enum IncomeSource {
    WAGES,
    SELF_EMPLOYMENT,
    INTEREST,
    DIVIDENDS,
    CAPITAL_GAINS,
    RENTAL,
    RETIREMENT_DISTRIBUTION,
    OTHER
}
