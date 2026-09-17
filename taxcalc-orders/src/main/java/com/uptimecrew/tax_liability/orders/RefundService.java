package com.uptimecrew.tax_liability.orders;

import java.math.BigDecimal;
import java.util.Objects;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Issues refunds, at most once per idempotency key.
 *
 * <p><strong>The guarantee and where it actually lives.</strong> The MCP tool's description
 * promises callers that retrying with the same key returns the original outcome rather than
 * debiting again. That promise is kept by a unique index on {@code (tenant_id,
 * idempotency_key)}, not by the code below. The code's job is to attempt the insert and, when
 * the index refuses it, read back what the winning call recorded.
 *
 * <p>Writing it the other way round - look up the key, and insert if nothing is found - reads
 * more naturally and is wrong. Two retries of the same request routinely arrive at once, because
 * retries are precisely what a caller does when the first response was slow; both lookups find
 * nothing, both inserts succeed, and the ledger is debited twice with no line of code having
 * misbehaved. The only participant that sees both statements is the database.
 *
 * <p><strong>The key is scoped to the tenant.</strong> A globally unique key would let one
 * tenant's UUID collide with another's and silently suppress a legitimate refund - a failure
 * that presents as missing money and is close to undiagnosable.
 */
@Service
// NOT `final`. The @Transactional method below is advised by a CGLIB proxy, and CGLIB subclasses
// its target - a final class fails at context startup. Removing @Transactional would make the
// class final again and would also remove the transaction boundary around the insert-then-read
// sequence, which is the wrong trade: without it, the read-back after a losing insert could run
// outside the transaction that the winning insert committed in.
public class RefundService {

    private static final Logger LOG = LoggerFactory.getLogger(RefundService.class);

    private final OrderRepository orders;
    private final RefundRepository refunds;

    /**
     * Creates the service.
     *
     * @param orders the order repository
     * @param refunds the refund repository
     * @throws NullPointerException if either argument is {@code null}
     */
    public RefundService(OrderRepository orders, RefundRepository refunds) {
        this.orders = Objects.requireNonNull(orders, "orders must not be null");
        this.refunds = Objects.requireNonNull(refunds, "refunds must not be null");
    }

    /**
     * Refunds an order, or returns the refund an earlier call with the same key already made.
     *
     * @param orderId the order to refund
     * @param tenantId the tenant that must own it
     * @param request the validated request body
     * @return the refund view, whether this call created it or an earlier one did
     * @throws OrderNotFoundException if the order does not exist for this tenant
     * @throws IllegalStateException if the insert was refused but the winning row cannot be read
     *     back, which would mean the unique constraint and the lookup disagree about what the key
     *     is - a broken invariant rather than a caller error, and not something to paper over
     */
    @Transactional
    public RefundView refund(String orderId, String tenantId, CreateRefundRequest request) {
        Objects.requireNonNull(orderId, "orderId must not be null");
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        Objects.requireNonNull(request, "request must not be null");

        if (!orders.existsForTenant(orderId, tenantId)) {
            throw new OrderNotFoundException(orderId);
        }

        BigDecimal amount = Money.normalise(request.amount());
        String refundId = "rfnd-" + UUID.randomUUID();

        boolean created =
                refunds.insertIfAbsent(
                        refundId,
                        orderId,
                        tenantId,
                        amount,
                        request.reason(),
                        request.idempotencyKey());

        if (created) {
            LOG.info(
                    "refund.created order={} tenant={} refund={} amount={}",
                    orderId,
                    tenantId,
                    refundId,
                    Money.render(amount));
            return RefundView.of(orderId, refundId, amount, request.reason(), "refunded");
        }

        RefundView existing =
                refunds.findByIdempotencyKey(tenantId, request.idempotencyKey())
                        .orElseThrow(
                                () ->
                                        new IllegalStateException(
                                                "idempotency key was rejected as a duplicate but no"
                                                    + " stored refund could be read back for it;"
                                                    + " the unique index and the lookup disagree"));
        LOG.info(
                "refund.replayed order={} tenant={} refund={} (idempotency key already used)",
                orderId,
                tenantId,
                existing.refundId());
        return existing;
    }
}
