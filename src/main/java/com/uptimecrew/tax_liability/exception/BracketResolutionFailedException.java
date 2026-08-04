package com.uptimecrew.tax_liability.exception;

/**
 * Thrown when a {@link com.uptimecrew.tax_liability.service.BracketResolver} strategy
 * cannot resolve a bracket because an underlying operation (e.g. loading bracket data)
 * failed.
 */
public final class BracketResolutionFailedException extends TaxLiabilityException {

    public BracketResolutionFailedException(String message) {
        super(message);
    }

    public BracketResolutionFailedException(String message, Throwable cause) {
        super(message, cause);
    }
}
