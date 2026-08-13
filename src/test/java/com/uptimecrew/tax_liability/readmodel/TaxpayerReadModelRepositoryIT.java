package com.uptimecrew.tax_liability.readmodel;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import java.util.List;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * Mongo counterpart to {@code TaxpayerRepositoryIT} (W2 D4): a {@code @DataMongoTest} against a
 * real Testcontainers Mongo, proving {@link TaxpayerReadModelRepository}'s derived finder.
 *
 * <p>Unlike {@code @DataJpaTest}, {@code @DataMongoTest} does not wrap each test method in a
 * rolled-back transaction, so every test explicitly clears the collection afterward to keep the
 * shared container's state from leaking between test methods.
 */
@Testcontainers
@DataMongoTest
class TaxpayerReadModelRepositoryIT {

    @Container
    @ServiceConnection
    static final MongoDBContainer MONGO = new MongoDBContainer("mongo:7");

    @Autowired
    TaxpayerReadModelRepository repository;

    @AfterEach
    void cleanUp() {
        repository.deleteAll();
    }

    @Test
    void save_and_find_round_trip() {
        // Arrange.
        TaxpayerReadModel document = new TaxpayerReadModel(
                "test-001", "Ada Lovelace", "SINGLE", "FEDERAL", Instant.now(), List.of());

        // Act.
        repository.save(document);

        // Assert.
        assertThat(repository.findById("test-001")).isPresent();
    }

    @Test
    void derived_finder_returns_only_taxpayers_matching_the_requested_filing_status() {
        // Arrange + Act.
        repository.save(new TaxpayerReadModel(
                "a", "Ada Lovelace", "SINGLE", "FEDERAL", Instant.now(), List.of()));
        repository.save(new TaxpayerReadModel(
                "b", "Grace Hopper", "MARRIED_FILING_JOINTLY", "FEDERAL", Instant.now(), List.of()));
        repository.save(new TaxpayerReadModel(
                "c", "Katherine Johnson", "SINGLE", "FEDERAL", Instant.now(), List.of()));

        // Act + Assert.
        assertThat(repository.findByFilingStatus("SINGLE"))
                .extracting(TaxpayerReadModel::getId)
                .containsExactlyInAnyOrder("a", "c");
    }
}
