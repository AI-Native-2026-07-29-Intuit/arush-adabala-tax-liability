package com.uptimecrew.tax_liability.exception;

/**
 * Thrown when a caller-supplied taxable amount is not valid input for bracket
 * resolution (e.g. negative).
 */
public final class InvalidIncomeException extends TaxLiabilityException {

    public InvalidIncomeException(String message) {
        super(message);
    }

    public InvalidIncomeException(String message, Throwable cause) {
        super(message, cause);
    }
}
