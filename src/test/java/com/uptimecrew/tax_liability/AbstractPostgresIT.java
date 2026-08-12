package com.uptimecrew.tax_liability;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.Statement;

import org.junit.jupiter.api.BeforeAll;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * Shared Testcontainers-backed Postgres for full-context {@code @SpringBootTest}s: since
 * JPA's {@code ddl-auto: validate} needs a real, schema-populated database to boot the
 * {@code EntityManagerFactory}, every full-context test needs one, not just the
 * repository-focused {@code @DataJpaTest}s.
 */
@Testcontainers
public abstract class AbstractPostgresIT {

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16-alpine");

    @BeforeAll
    static void applySchema() throws Exception {
        try (Connection conn = TestPostgresConnections.openWithRetry(POSTGRES);
                Statement stmt = conn.createStatement()) {
            stmt.execute(Files.readString(Path.of("db/V1__schema.sql")));
        }
    }
}
