package com.uptimecrew.tax_liability.exception;

/**
 * Root of the taxcalc domain exception hierarchy. Abstract so callers must throw one of
 * the concrete subclasses, not the base, while still being able to write a single
 * {@code catch (TaxLiabilityException ex)} to handle every taxcalc-domain failure.
 *
 * Unchecked (extends RuntimeException) because taxcalc failure modes are either
 * programmer errors at the call site or transient upstream failures the caller cannot
 * reasonably handle synchronously — logging and retry at the service boundary is the
 * recovery model.
 */
public abstract class TaxLiabilityException extends RuntimeException {

    protected TaxLiabilityException(String message) {
        super(message);
    }

    protected TaxLiabilityException(String message, Throwable cause) {
        super(message, cause);
    }
}
