package com.uptimecrew.tax_liability.orders;

import java.util.Map;
import java.util.Objects;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * The two endpoints the MCP surface's {@code orders.*} tools call.
 *
 * <p>Thin on purpose: the controller resolves the tenant, delegates, and shapes the response.
 * The refund rule lives in {@link RefundService}, where it is testable without a servlet.
 */
@RestController
@RequestMapping("/orders")
public final class OrderController {

    private final OrderRepository orders;
    private final RefundRepository refundRows;
    private final RefundService refunds;

    /**
     * Creates the controller.
     *
     * @param orders the order repository
     * @param refundRows the refund repository, read by the ledger-inspection endpoint
     * @param refunds the refund service
     * @throws NullPointerException if any argument is {@code null}
     */
    public OrderController(
            OrderRepository orders, RefundRepository refundRows, RefundService refunds) {
        this.orders = Objects.requireNonNull(orders, "orders must not be null");
        this.refundRows = Objects.requireNonNull(refundRows, "refundRows must not be null");
        this.refunds = Objects.requireNonNull(refunds, "refunds must not be null");
    }

    /**
     * Reads one order belonging to the calling tenant.
     *
     * @param orderId the order to read
     * @param tenantId the calling tenant, from {@code X-Tenant}
     * @return 200 with the order, or 404 when it does not exist for this tenant
     */
    @GetMapping("/{orderId}")
    public ResponseEntity<OrderView> getOrder(
            @PathVariable String orderId, @RequestHeader("X-Tenant") String tenantId) {
        return orders.findByIdForTenant(orderId, tenantId)
                .map(ResponseEntity::ok)
                .orElseThrow(() -> new OrderNotFoundException(orderId));
    }

    /**
     * Refunds an order, absorbing retries that carry an idempotency key already used.
     *
     * <p><strong>The header wins over the body field when both are present.</strong> They are
     * normally the same value - the MCP tool sends the same UUID twice on purpose - but if they
     * ever disagree, the header is what a proxy or retry layer between the caller and here would
     * have keyed its own deduplication on, so honouring it keeps this service's notion of "the
     * same request" aligned with theirs. Preferring the body would let a replayed request be
     * treated as new by exactly the infrastructure most likely to replay it.
     *
     * @param orderId the order to refund
     * @param tenantId the calling tenant, from {@code X-Tenant}
     * @param headerKey the {@code Idempotency-Key} header, if sent
     * @param body the request body
     * @return 200 with the refund view
     */
    @PostMapping("/{orderId}/refunds")
    public ResponseEntity<RefundView> createRefund(
            @PathVariable String orderId,
            @RequestHeader("X-Tenant") String tenantId,
            @RequestHeader(value = "Idempotency-Key", required = false) String headerKey,
            @RequestBody CreateRefundRequest body) {

        String key = (headerKey != null && !headerKey.isBlank()) ? headerKey : body.idempotencyKey();
        CreateRefundRequest effective =
                new CreateRefundRequest(orderId, body.amount(), body.reason(), key);
        return ResponseEntity.ok(refunds.refund(orderId, tenantId, effective));
    }

    /**
     * Reports how many refunds the calling tenant has recorded against an idempotency key.
     *
     * <p><strong>Why this endpoint exists.</strong> The idempotency guarantee is only credible if
     * something outside this service can check it. A caller comparing two {@code refund_id}
     * values learns that it was told the same thing twice, which a service that issued two
     * refunds and rendered them identically would also produce. The row count is the question
     * whose answer cannot be faked by the response shape, and the end-to-end test asserts on it.
     *
     * <p>Tenant-scoped like every other read here, so it cannot be used to probe whether another
     * tenant has used a given key.
     *
     * @param orderId the order, present so the route reads naturally; the count is keyed on the
     *     idempotency key, which is unique per tenant regardless of order
     * @param tenantId the calling tenant, from {@code X-Tenant}
     * @param idempotencyKey the key to count against
     * @return 200 with {@code {"count": n}}, where n is 0 or 1 by construction
     */
    @GetMapping("/{orderId}/refunds")
    public ResponseEntity<Map<String, Integer>> countRefunds(
            @PathVariable String orderId,
            @RequestHeader("X-Tenant") String tenantId,
            @RequestParam("idempotency_key") String idempotencyKey) {
        if (!orders.existsForTenant(orderId, tenantId)) {
            throw new OrderNotFoundException(orderId);
        }
        return ResponseEntity.ok(
                Map.of("count", refundRows.countForIdempotencyKey(tenantId, idempotencyKey)));
    }
}
