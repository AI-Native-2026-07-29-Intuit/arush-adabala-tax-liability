package com.uptimecrew.tax_liability.repository;

import static org.assertj.core.api.Assertions.assertThat;

import com.uptimecrew.tax_liability.entity.Taxpayer;

import java.time.Instant;
import java.util.Optional;

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

        // Act + Assert. Flyway's V2__seed.sql (W3 D3) also seeds SINGLE taxpayers into every
        // Postgres-backed test's database, so this asserts "a" is included and "b" excluded
        // rather than an exact set - proving the filter, not an empty-table assumption.
        assertThat(repository.findByFilingStatus("SINGLE"))
                .extracting(Taxpayer::getId)
                .contains("a")
                .doesNotContain("b");
    }
}
