package com.uptimecrew.tax_liability.repository;

import static org.assertj.core.api.Assertions.assertThat;

import com.uptimecrew.tax_liability.entity.Taxpayer;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.Statement;
import java.time.Instant;
import java.util.Optional;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

// @DataJpaTest by default replaces the DataSource with an in-memory H2.
// @AutoConfigureTestDatabase(replace=NONE) keeps the real Testcontainers one.
// @ServiceConnection wires the container's URL/user/password into Spring.
@Testcontainers
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
class TaxpayerRepositoryIT {

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> PG = new PostgreSQLContainer<>("postgres:16-alpine");

    @BeforeAll
    static void applySchema() throws Exception {
        try (Connection conn = DriverManager.getConnection(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword());
                Statement stmt = conn.createStatement()) {
            stmt.execute(Files.readString(Path.of("db/V1__schema.sql")));
        }
    }

    @Autowired
    TaxpayerRepository repository;

    @Test
    void save_and_find_round_trip() {
        // Arrange.
        Taxpayer entity = new Taxpayer("test-001", "Ada Lovelace", "SINGLE", "FEDERAL", Instant.now());

        // Act.
        repository.save(entity);
        Optional<Taxpayer> found = repository.findById("test-001");

        // Assert.
        assertThat(found).isPresent();
        assertThat(found.get().getId()).isEqualTo("test-001");
        assertThat(found.get().getDisplayName()).isEqualTo("Ada Lovelace");
        assertThat(found.get().getFilingStatus()).isEqualTo("SINGLE");
        assertThat(found.get().getHomeJurisdiction()).isEqualTo("FEDERAL");
    }

    @Test
    void derived_finder_returns_only_matching_rows() {
        // Arrange + Act.
        repository.save(new Taxpayer("a", "Ada Lovelace", "SINGLE", "FEDERAL", Instant.now()));
        repository.save(new Taxpayer("b", "Grace Hopper", "MARRIED_FILING_JOINTLY", "FEDERAL", Instant.now()));

        // Act + Assert.
        assertThat(repository.findByFilingStatus("SINGLE"))
                .extracting(Taxpayer::getId)
                .containsExactlyInAnyOrder("a");
    }
}
