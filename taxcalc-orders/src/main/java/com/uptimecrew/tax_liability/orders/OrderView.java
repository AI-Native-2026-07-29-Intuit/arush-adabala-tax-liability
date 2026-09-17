package com.uptimecrew.tax_liability.orders;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.math.BigDecimal;
import java.util.Objects;

/**
 * The read model returned by {@code GET /orders/{orderId}}.
 *
 * <p><strong>The field names are snake_case on purpose, and they are a contract.</strong> The
 * consumer is a Python MCP server whose Pydantic model declares {@code extra="forbid"}, so a
 * field renamed here does not degrade gracefully on the other side - it fails validation and the
 * tool call errors. {@code taxcalc-mcp-server/tests/test_e2e_mcp_to_spring.py} is what makes
 * that agreement a test rather than a hope.
 *
 * <p><strong>{@code total} is a {@link BigDecimal} and serialises as a JSON string.</strong>
 * Jackson would otherwise render it as a JSON number, and the moment a money value is a JSON
 * number its exactness belongs to whichever float parser reads it first. The string form also
 * preserves the scale, and {@code 10.00} and {@code 10} are the same number but a different
 * money value.
 *
 * @param orderId the order's identifier
 * @param tenantId the owning tenant
 * @param total the order total, always at scale 2
 * @param status the order's current status
 */
public record OrderView(
        @JsonProperty("order_id") String orderId,
        @JsonProperty("tenant_id") String tenantId,
        @JsonProperty("total") String total,
        @JsonProperty("status") String status) {

    /**
     * Validates the view's invariants.
     *
     * @throws NullPointerException if any argument is {@code null}
     * @throws IllegalArgumentException if any string argument is blank
     */
    public OrderView {
        Objects.requireNonNull(orderId, "orderId must not be null");
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        Objects.requireNonNull(total, "total must not be null");
        Objects.requireNonNull(status, "status must not be null");
        if (orderId.isBlank()) {
            throw new IllegalArgumentException("orderId must not be blank");
        }
        if (tenantId.isBlank()) {
            throw new IllegalArgumentException("tenantId must not be blank");
        }
        if (status.isBlank()) {
            throw new IllegalArgumentException("status must not be blank");
        }
    }

    /**
     * Builds a view from a row, rendering the money value as an exact decimal string.
     *
     * @param orderId the order's identifier
     * @param tenantId the owning tenant
     * @param total the order total
     * @param status the order's current status
     * @return the view
     */
    public static OrderView of(String orderId, String tenantId, BigDecimal total, String status) {
        return new OrderView(orderId, tenantId, Money.render(total), status);
    }
}
