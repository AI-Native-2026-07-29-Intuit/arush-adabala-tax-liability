package com.uptimecrew.tax_liability.orders;

import java.math.BigDecimal;
import java.util.Objects;

/**
 * The request body of {@code POST /orders/{orderId}/refunds}.
 *
 * <p><strong>The body is camelCase while the responses are snake_case</strong>, which looks like
 * an inconsistency and is a deliberate contract. The MCP tool sends {@code idempotencyKey} in the
 * body and {@code Idempotency-Key} as a header, and both spellings are load-bearing: the body
 * field is what this service persists alongside the ledger entry, and the header is what any
 * proxy, retry middleware or service mesh between the two reads. Renaming either half silently
 * removes one of the two places a duplicate can be caught.
 *
 * <p>{@code amount} is declared {@link BigDecimal} so Jackson parses the JSON string
 * {@code "10.00"} into an exact value. Declaring it {@code double} would discard the caller's
 * precision at the edge of the service, before any of this project's money rules could apply.
 *
 * @param orderId the order to refund; echoed by the caller and cross-checked against the path
 * @param amount the amount to refund
 * @param reason why the refund is being issued
 * @param idempotencyKey the caller's UUID v4; retries carrying it are absorbed
 */
public record CreateRefundRequest(
        String orderId, BigDecimal amount, String reason, String idempotencyKey) {

    /** The shortest reason worth recording; matches the MCP tool's own schema. */
    private static final int MIN_REASON_LENGTH = 4;

    /** The longest reason a ledger row will hold; matches the column width. */
    private static final int MAX_REASON_LENGTH = 200;

    /**
     * Validates the request's invariants.
     *
     * <p>Validation lives in the compact constructor rather than in the controller so that a
     * request object cannot exist in an invalid state at all - there is no window in which some
     * other code path could construct one and skip the checks.
     *
     * @throws NullPointerException if any argument is {@code null}
     * @throws IllegalArgumentException if any argument is nonsensical
     */
    public CreateRefundRequest {
        Objects.requireNonNull(orderId, "orderId must not be null");
        Objects.requireNonNull(amount, "amount must not be null");
        Objects.requireNonNull(reason, "reason must not be null");
        Objects.requireNonNull(idempotencyKey, "idempotencyKey must not be null");
        if (orderId.isBlank()) {
            throw new IllegalArgumentException("orderId must not be blank");
        }
        if (idempotencyKey.isBlank()) {
            throw new IllegalArgumentException("idempotencyKey must not be blank");
        }
        if (reason.length() < MIN_REASON_LENGTH || reason.length() > MAX_REASON_LENGTH) {
            throw new IllegalArgumentException(
                    "reason must be between "
                            + MIN_REASON_LENGTH
                            + " and "
                            + MAX_REASON_LENGTH
                            + " characters, got "
                            + reason.length());
        }
        if (amount.signum() <= 0) {
            throw new IllegalArgumentException("amount must be greater than zero, got " + amount);
        }
    }
}
