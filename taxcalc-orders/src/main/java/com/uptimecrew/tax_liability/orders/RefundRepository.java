package com.uptimecrew.tax_liability.orders;

import java.math.BigDecimal;
import java.util.Objects;
import java.util.Optional;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/**
 * Writes and reads refund ledger rows.
 *
 * <p>The insert below is the whole idempotency mechanism. See
 * {@link #insertIfAbsent(String, String, String, BigDecimal, String, String)}.
 */
@Repository
// NOT `final` - proxied by Spring's persistence exception translator. See OrderRepository for the
// full reasoning and the alternatives that were weighed.
public class RefundRepository {

    private final JdbcTemplate jdbc;

    /**
     * Creates the repository.
     *
     * @param jdbc the template to query through
     * @throws NullPointerException if {@code jdbc} is {@code null}
     */
    public RefundRepository(JdbcTemplate jdbc) {
        this.jdbc = Objects.requireNonNull(jdbc, "jdbc must not be null");
    }

    /**
     * Inserts a refund, or does nothing if this tenant already used this idempotency key.
     *
     * <p><strong>{@code ON CONFLICT DO NOTHING} is doing the work that a "check whether this key
     * was used, then insert" would appear to do and would not.</strong> Two retries arriving
     * concurrently - which is the normal case, not the exotic one, since retries are what happens
     * when the first response was slow - both run the check, both find nothing, and both insert.
     * The ledger is then debited twice and every line of application code involved looks correct.
     * Pushing the decision into the unique index makes the database the arbiter, and the database
     * is the only participant that sees both statements.
     *
     * @param refundId the identifier to assign if this insert wins
     * @param orderId the order being refunded
     * @param tenantId the owning tenant
     * @param amount the amount, already normalised to money scale
     * @param reason why the refund is being issued
     * @param idempotencyKey the caller's key
     * @return {@code true} when this call created the row, {@code false} when an earlier call
     *     with the same key already had
     */
    public boolean insertIfAbsent(
            String refundId,
            String orderId,
            String tenantId,
            BigDecimal amount,
            String reason,
            String idempotencyKey) {
        Objects.requireNonNull(refundId, "refundId must not be null");
        Objects.requireNonNull(orderId, "orderId must not be null");
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        Objects.requireNonNull(amount, "amount must not be null");
        Objects.requireNonNull(reason, "reason must not be null");
        Objects.requireNonNull(idempotencyKey, "idempotencyKey must not be null");

        int inserted =
                jdbc.update(
                        "INSERT INTO refunds "
                                + "(refund_id, order_id, tenant_id, amount, reason, status, "
                                + " idempotency_key) "
                                + "VALUES (?, ?, ?, ?, ?, 'refunded', ?) "
                                + "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING",
                        refundId,
                        orderId,
                        tenantId,
                        amount,
                        reason,
                        idempotencyKey);
        return inserted == 1;
    }

    /**
     * Finds the refund a tenant recorded against an idempotency key.
     *
     * @param tenantId the owning tenant
     * @param idempotencyKey the key
     * @return the stored refund, or empty when the key is unused
     */
    public Optional<RefundView> findByIdempotencyKey(String tenantId, String idempotencyKey) {
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        Objects.requireNonNull(idempotencyKey, "idempotencyKey must not be null");
        try {
            return Optional.ofNullable(
                    jdbc.queryForObject(
                            "SELECT order_id, refund_id, amount, reason, status FROM refunds "
                                    + "WHERE tenant_id = ? AND idempotency_key = ?",
                            (rs, rowNum) ->
                                    RefundView.of(
                                            rs.getString("order_id"),
                                            rs.getString("refund_id"),
                                            rs.getBigDecimal("amount"),
                                            rs.getString("reason"),
                                            rs.getString("status")),
                            tenantId,
                            idempotencyKey));
        } catch (EmptyResultDataAccessException notFound) {
            return Optional.empty();
        }
    }

    /**
     * Counts the refund rows a tenant recorded against one idempotency key.
     *
     * <p>Either 0 or 1 by construction - the unique index permits nothing else - which is exactly
     * what makes it worth exposing. It is the only question whose answer distinguishes "the retry
     * was absorbed" from "a second refund was issued and rendered identically", and a caller that
     * cannot ask it has to take the guarantee on trust.
     *
     * @param tenantId the owning tenant
     * @param idempotencyKey the key to count against
     * @return how many refund rows carry that key for this tenant
     */
    public int countForIdempotencyKey(String tenantId, String idempotencyKey) {
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        Objects.requireNonNull(idempotencyKey, "idempotencyKey must not be null");
        Integer count =
                jdbc.queryForObject(
                        "SELECT COUNT(*) FROM refunds WHERE tenant_id = ? AND idempotency_key = ?",
                        Integer.class,
                        tenantId,
                        idempotencyKey);
        return count == null ? 0 : count;
    }

    /**
     * Counts every refund row recorded against an order.
     *
     * <p>Exists for the tests. Matching refund ids prove the caller was told the same thing
     * twice; only the row count proves the ledger was debited once.
     *
     * @param orderId the order
     * @param tenantId the owning tenant
     * @return how many refund rows exist
     */
    public int countForOrder(String orderId, String tenantId) {
        Objects.requireNonNull(orderId, "orderId must not be null");
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        Integer count =
                jdbc.queryForObject(
                        "SELECT COUNT(*) FROM refunds WHERE order_id = ? AND tenant_id = ?",
                        Integer.class,
                        orderId,
                        tenantId);
        return count == null ? 0 : count;
    }
}
