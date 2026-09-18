package com.uptimecrew.tax_liability.orders;

import java.math.BigDecimal;
import java.util.Objects;
import java.util.Optional;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/**
 * Reads orders.
 *
 * <p><strong>Every query is filtered by tenant, and that is a security boundary rather than a
 * convenience.</strong> An order id is not a secret and not unguessable; without the tenant
 * predicate, knowing an id would be enough to read another tenant's order. The filter is applied
 * in the SQL rather than checked after the row is fetched, because a check after the fetch is a
 * check someone can forget while the query still looks correct.
 */
@Repository
// NOT `final`, and that is a framework requirement rather than a lapse in this project's
// final-by-default convention. Spring's PersistenceExceptionTranslationPostProcessor wraps every
// @Repository bean in a CGLIB proxy so that driver-specific SQLExceptions surface as Spring's
// DataAccessException hierarchy - which is what lets findByIdForTenant catch
// EmptyResultDataAccessException instead of parsing a PSQLException. CGLIB subclasses the target,
// so a final class fails at context startup with "Cannot subclass final class".
//
// The alternatives were weighed: dropping @Repository for @Component keeps the class final and
// silently disables the translation the code already depends on; declaring an interface for the
// proxy to implement adds a second type per repository to satisfy a framework detail. Extension
// by the container is a legitimate reason to be non-final, which is precisely the exemption the
// convention names.
public class OrderRepository {

    private final JdbcTemplate jdbc;

    /**
     * Creates the repository.
     *
     * @param jdbc the template to query through
     * @throws NullPointerException if {@code jdbc} is {@code null}
     */
    public OrderRepository(JdbcTemplate jdbc) {
        this.jdbc = Objects.requireNonNull(jdbc, "jdbc must not be null");
    }

    /**
     * Finds one order belonging to a tenant.
     *
     * @param orderId the order to read
     * @param tenantId the tenant that must own it
     * @return the order, or empty when it does not exist or belongs to another tenant - the two
     *     cases are deliberately indistinguishable to the caller, because telling them apart
     *     would confirm the existence of another tenant's order
     */
    public Optional<OrderView> findByIdForTenant(String orderId, String tenantId) {
        Objects.requireNonNull(orderId, "orderId must not be null");
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        try {
            return Optional.ofNullable(
                    jdbc.queryForObject(
                            "SELECT order_id, tenant_id, total, status FROM orders "
                                    + "WHERE order_id = ? AND tenant_id = ?",
                            (rs, rowNum) ->
                                    OrderView.of(
                                            rs.getString("order_id"),
                                            rs.getString("tenant_id"),
                                            rs.getBigDecimal("total"),
                                            rs.getString("status")),
                            orderId,
                            tenantId));
        } catch (EmptyResultDataAccessException notFound) {
            return Optional.empty();
        }
    }

    /**
     * Returns whether an order exists for a tenant.
     *
     * @param orderId the order
     * @param tenantId the tenant that must own it
     * @return {@code true} when the row exists
     */
    public boolean existsForTenant(String orderId, String tenantId) {
        Objects.requireNonNull(orderId, "orderId must not be null");
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        Integer count =
                jdbc.queryForObject(
                        "SELECT COUNT(*) FROM orders WHERE order_id = ? AND tenant_id = ?",
                        Integer.class,
                        orderId,
                        tenantId);
        return count != null && count > 0;
    }

    /**
     * Returns the total refunded against an order so far.
     *
     * <p>Used only by tests and diagnostics; the refund path does not consult it, because a
     * read-then-write over-refund check has the same race the idempotency key has - see
     * {@link RefundService}.
     *
     * @param orderId the order
     * @param tenantId the tenant that owns it
     * @return the summed refund amount, at money scale, zero when there are none
     */
    public BigDecimal refundedTotal(String orderId, String tenantId) {
        Objects.requireNonNull(orderId, "orderId must not be null");
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        BigDecimal sum =
                jdbc.queryForObject(
                        "SELECT COALESCE(SUM(amount), 0) FROM refunds "
                                + "WHERE order_id = ? AND tenant_id = ?",
                        BigDecimal.class,
                        orderId,
                        tenantId);
        return sum == null ? BigDecimal.ZERO.setScale(Money.SCALE) : sum.setScale(Money.SCALE);
    }
}
