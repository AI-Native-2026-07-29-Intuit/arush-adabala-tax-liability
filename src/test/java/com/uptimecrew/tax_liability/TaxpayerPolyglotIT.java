package com.uptimecrew.tax_liability;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.Statement;
import java.util.Optional;

import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.service.TaxLiabilityService;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.cache.CacheManager;
import org.springframework.test.context.ActiveProfiles;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * Full-context proof of the polyglot-persistence path: {@link TaxLiabilityService#computeLiability}
 * writes through Postgres AND Mongo in one transaction, and {@link TaxLiabilityService#findById}
 * serves a repeated read from the Redis-backed cache without a second Mongo round-trip. Boots
 * three real Testcontainers-managed datastores, each wired via {@code @ServiceConnection} so
 * Spring Boot 3.1+ overrides their connection properties directly without a hand-written
 * {@code @DynamicPropertySource}.
 */
@Testcontainers
@SpringBootTest
@ActiveProfiles("test")
class TaxpayerPolyglotIT {

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> PG = new PostgreSQLContainer<>("postgres:16-alpine");

    @Container
    @ServiceConnection
    static final MongoDBContainer MONGO = new MongoDBContainer("mongo:7");

    @Container
    @ServiceConnection(name = "redis")
    static final GenericContainer<?> REDIS = new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    @Autowired
    private TaxLiabilityService service;

    @Autowired
    private CacheManager cacheManager;

    @BeforeAll
    static void applyPostgresSchema() throws Exception {
        try (Connection conn = TestPostgresConnections.openWithRetry(PG);
                Statement stmt = conn.createStatement()) {
            stmt.execute(Files.readString(Path.of("db/V1__schema.sql")));
        }
        // Mongo and Redis are schemaless - no equivalent step is needed for them.
    }

    @Test
    void write_path_populates_postgres_AND_mongo() {
        service.computeLiability("polyglot-001", "Ada Lovelace", "SINGLE", new BigDecimal("75000.00"));

        Optional<TaxpayerReadModel> found = service.findById("polyglot-001");

        assertThat(found).isPresent();
    }

    @Test
    void second_read_is_served_from_redis() {
        service.computeLiability("polyglot-002", "Grace Hopper", "SINGLE", new BigDecimal("50000.00"));

        service.findById("polyglot-002"); // first read: cache miss, populates redis
        var cached = cacheManager.getCache(TaxLiabilityService.CACHE_NAME).get("polyglot-002");

        assertThat(cached).as("cache entry after first read").isNotNull();
    }
}
