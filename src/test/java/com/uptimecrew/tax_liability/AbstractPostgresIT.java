package com.uptimecrew.tax_liability;

import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * Shared Testcontainers-backed Postgres for full-context {@code @SpringBootTest}s: since
 * JPA's {@code ddl-auto: validate} needs a real, schema-populated database to boot the
 * {@code EntityManagerFactory}, every full-context test needs one, not just the
 * repository-focused {@code @DataJpaTest}s. Schema is applied by Flyway automatically
 * during context startup (W3 D3) - no manual JDBC setup step needed here.
 */
@Testcontainers
public abstract class AbstractPostgresIT {

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16-alpine");
}
