package com.uptimecrew.tax_liability.orders;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.math.BigDecimal;
import java.util.Objects;

/**
 * The read model returned by {@code POST /orders/{orderId}/refunds}.
 *
 * <p>{@code refundId} is what makes the idempotency guarantee observable. A caller that retries
 * with the same key and compares the two {@code refund_id} values is asking the only question
 * that distinguishes "the retry was absorbed" from "a second refund was issued", and it is the
 * assertion the end-to-end test is built around.
 *
 * <p>See {@link OrderView} for why the names are snake_case and why {@code amount} is a string.
 *
 * @param orderId the refunded order
 * @param refundId the refund's identifier
 * @param amount the refunded amount, always at scale 2
 * @param reason why the refund was issued
 * @param status the refund's status
 */
public record RefundView(
        @JsonProperty("order_id") String orderId,
        @JsonProperty("refund_id") String refundId,
        @JsonProperty("amount") String amount,
        @JsonProperty("reason") String reason,
        @JsonProperty("status") String status) {

    /**
     * Validates the view's invariants.
     *
     * @throws NullPointerException if any argument is {@code null}
     * @throws IllegalArgumentException if any identifier is blank
     */
    public RefundView {
        Objects.requireNonNull(orderId, "orderId must not be null");
        Objects.requireNonNull(refundId, "refundId must not be null");
        Objects.requireNonNull(amount, "amount must not be null");
        Objects.requireNonNull(reason, "reason must not be null");
        Objects.requireNonNull(status, "status must not be null");
        if (orderId.isBlank()) {
            throw new IllegalArgumentException("orderId must not be blank");
        }
        if (refundId.isBlank()) {
            throw new IllegalArgumentException("refundId must not be blank");
        }
    }

    /**
     * Builds a view from a row, rendering the money value as an exact decimal string.
     *
     * @param orderId the refunded order
     * @param refundId the refund's identifier
     * @param amount the refunded amount
     * @param reason why the refund was issued
     * @param status the refund's status
     * @return the view
     */
    public static RefundView of(
            String orderId, String refundId, BigDecimal amount, String reason, String status) {
        return new RefundView(orderId, refundId, Money.render(amount), reason, status);
    }
}
