package com.uptimecrew.tax_liability;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel.EmbeddedLiability;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.graphql.tester.AutoConfigureGraphQlTester;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.graphql.test.tester.GraphQlTester;
import org.springframework.test.context.ActiveProfiles;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.junit.jupiter.Container;

/**
 * Proves the W3 D5 {@code Taxpayer.tags} field and {@code taxpayersByTag} query end to end
 * against a real Spring for GraphQL server backed by a real Mongo read model, mirroring the
 * container/seed pattern established in {@link TaxpayerGraphQlIT}: a taxpayer seeded with tags
 * round-trips them through GraphQL, {@code taxpayersByTag} filters correctly, and a taxpayer
 * seeded via the pre-existing 6-arg {@link TaxpayerReadModel} constructor (no tags argument)
 * still resolves an empty {@code tags} list rather than null or a GraphQL error.
 */
@SpringBootTest
@AutoConfigureGraphQlTester
@ActiveProfiles("test")
class TaxpayerTagsGraphQlIT extends AbstractPostgresIT {

    @Container
    @ServiceConnection
    static final MongoDBContainer MONGO = new MongoDBContainer("mongo:7");

    // TaxLiabilityService.findById (behind the `taxpayer` query) is @Cacheable against Redis.
    @Container
    @ServiceConnection(name = "redis")
    static final GenericContainer<?> REDIS = new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    @Autowired
    GraphQlTester graphQlTester;

    @Autowired
    TaxpayerReadModelRepository readModelRepository;

    @BeforeEach
    void seedTaxpayers() {
        List<EmbeddedLiability> liabilities = List.of(new EmbeddedLiability(2026, "fed-2026-10pct",
                new BigDecimal("9500.00"), new BigDecimal("950.00"), Instant.now()));

        // Tagged with both "vip" and "audit-flagged" - via the new 7-arg constructor.
        readModelRepository.save(new TaxpayerReadModel("tag-vip-1", "VIP Taxpayer One", "SINGLE", "FEDERAL",
                Instant.now().minusSeconds(3), liabilities, List.of("vip", "audit-flagged")));

        // Tagged with only "vip" - via the new 7-arg constructor.
        readModelRepository.save(new TaxpayerReadModel("tag-vip-2", "VIP Taxpayer Two", "SINGLE", "FEDERAL",
                Instant.now().minusSeconds(2), liabilities, List.of("vip")));

        // Tagged with something else entirely - must NOT show up in a "vip" search.
        readModelRepository.save(new TaxpayerReadModel("tag-other-1", "Archived Taxpayer", "SINGLE", "FEDERAL",
                Instant.now().minusSeconds(1), liabilities, List.of("archived")));

        // Seeded via the OLD 6-arg constructor (no tags argument at all) - proves the
        // backward-compatible overload defaults to an empty list end to end, not just in Java.
        readModelRepository.save(new TaxpayerReadModel("tag-legacy-1", "Legacy Taxpayer", "SINGLE", "FEDERAL",
                Instant.now(), liabilities));
    }

    @AfterEach
    void cleanUp() {
        readModelRepository.deleteAll();
    }

    @Test
    void taxpayer_returnsSeededTags() {
        List<String> tags = graphQlTester.document("query { taxpayer(id: \"tag-vip-1\") { tags } }")
                .execute()
                .path("taxpayer.tags").entityList(String.class)
                .get();

        assertThat(tags).containsExactlyInAnyOrder("vip", "audit-flagged");
    }

    @Test
    void taxpayersByTag_returnsExactlyMatchingTaxpayers() {
        List<String> ids = graphQlTester.document("query { taxpayersByTag(tag: \"vip\") { id } }")
                .execute()
                .path("taxpayersByTag[*].id").entityList(String.class)
                .get();

        assertThat(ids).containsExactlyInAnyOrder("tag-vip-1", "tag-vip-2");
    }

    @Test
    void taxpayer_seededViaOldConstructor_returnsEmptyTagsNotNull() {
        GraphQlTester.Response response = graphQlTester
                .document("query { taxpayer(id: \"tag-legacy-1\") { id tags } }")
                .execute();

        response.errors().verify();
        response.path("taxpayer.id").entity(String.class).isEqualTo("tag-legacy-1");
        List<String> tags = response.path("taxpayer.tags").entityList(String.class).get();

        assertThat(tags).isNotNull().isEmpty();
    }
}
