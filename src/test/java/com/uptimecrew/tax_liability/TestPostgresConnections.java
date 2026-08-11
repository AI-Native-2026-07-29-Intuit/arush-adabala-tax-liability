package com.uptimecrew.tax_liability;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;

import org.testcontainers.containers.PostgreSQLContainer;

/**
 * Opens a JDBC connection to a Testcontainers-managed Postgres, retrying briefly: the
 * container's Postgres process can report itself ready before the host-side port-forward
 * (e.g. Rancher Desktop's proxy) actually accepts connections, so the very first connect
 * attempt can be refused even though the container is healthy.
 */
public final class TestPostgresConnections {

    private static final int MAX_ATTEMPTS = 10;
    private static final long RETRY_DELAY_MILLIS = 500L;

    private TestPostgresConnections() {
        throw new AssertionError("TestPostgresConnections is not instantiable");
    }

    public static Connection openWithRetry(PostgreSQLContainer<?> container) throws SQLException {
        SQLException lastFailure = null;
        for (int attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
            try {
                return DriverManager.getConnection(container.getJdbcUrl(), container.getUsername(), container.getPassword());
            } catch (SQLException ex) {
                lastFailure = ex;
                try {
                    Thread.sleep(RETRY_DELAY_MILLIS);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    throw lastFailure;
                }
            }
        }
        throw lastFailure;
    }
}
