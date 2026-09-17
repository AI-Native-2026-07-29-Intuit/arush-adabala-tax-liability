package com.uptimecrew.tax_liability.orders;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * The {@code taxcalc-orders} service: order lookup and idempotent refunds over HTTP.
 *
 * <p>This is the service the MCP surface's {@code orders.*} tools forward to. It exists as its
 * own deployable rather than as a slice of the main taxcalc monolith for one reason: the
 * end-to-end test that matters needs to start it, a database, and the MCP server, and assert
 * they agree. The monolith cannot reach a healthy state without MongoDB, Redis, Kafka and an
 * OAuth2 issuer, which would make that test a nine-container affair testing mostly
 * infrastructure.
 *
 * <p><strong>What this service is authoritative for.</strong> The refund ledger. The
 * idempotency guarantee that the MCP tool advertises is enforced here, by a unique constraint in
 * Postgres - not by the caller remembering to send a key, and not by application code checking
 * first and inserting second. See {@link RefundService} for why that distinction decides whether
 * the guarantee survives two concurrent retries.
 */
@SpringBootApplication
public class TaxcalcOrdersApplication {

    /**
     * Boots the service.
     *
     * @param args standard Spring Boot arguments; none are interpreted by this class.
     */
    public static void main(String[] args) {
        SpringApplication.run(TaxcalcOrdersApplication.class, args);
    }
}
