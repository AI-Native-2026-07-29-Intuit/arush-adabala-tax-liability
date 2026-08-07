package com.uptimecrew.tax_liability.repository;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

@Testcontainers
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class TaxpayerQueryIT {

    private static final String INTENTIONAL_FAILURE_MARKER = "-- Intentional failure test";

    @Container
    private static final PostgreSQLContainer<?> PG = new PostgreSQLContainer<>("postgres:16-alpine");

    @BeforeAll
    void applySchemaAndSeed() throws Exception {
        try (Connection conn = openConnection(); Statement stmt = conn.createStatement()) {
            stmt.execute(Files.readString(Path.of("db/V1__schema.sql")));
            stmt.execute(seedSqlWithoutIntentionalFailureBlock());
        }
    }

    @Test
    void cteQuery_returnsOnlyTaxpayersWithPositiveTotalLiabilityOrderedDescending() throws Exception {
        List<BigDecimal> totals = new ArrayList<>();
        try (Connection conn = openConnection();
                Statement stmt = conn.createStatement();
                ResultSet rs = stmt.executeQuery(Files.readString(Path.of("db/queries/cte.sql")))) {
            while (rs.next()) {
                totals.add(rs.getBigDecimal("total_liability"));
            }
        }

        assertThat(totals)
                .as("CTE result: total_liability per taxpayer, filtered to > 0.00")
                .isNotEmpty()
                .doesNotContainNull()
                .isSortedAccordingTo((a, b) -> b.compareTo(a));
        assertThat(totals).allSatisfy(total -> assertThat(total).isGreaterThan(BigDecimal.ZERO));
    }

    @Test
    void windowQuery_ranksEachTaxpayerWithinItsFilingStatusWithoutCollapsingRows() throws Exception {
        List<Integer> ranks = new ArrayList<>();
        try (Connection conn = openConnection();
                Statement stmt = conn.createStatement();
                ResultSet rs = stmt.executeQuery(Files.readString(Path.of("db/queries/window.sql")))) {
            while (rs.next()) {
                ranks.add(rs.getInt("amount_rank"));
            }
        }

        assertThat(ranks)
                .as("window query returns one row per liability, not one row per filing_status group")
                .hasSize(5)
                .contains(1, 2);
    }

    private Connection openConnection() throws Exception {
        return DriverManager.getConnection(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword());
    }

    // V2__seed.sql intentionally ends with a second transaction that violates a CHECK
    // constraint to demonstrate constraint enforcement (see its own header comment);
    // that block is meant to be run manually, not applied by test setup, so it is stripped here.
    private String seedSqlWithoutIntentionalFailureBlock() throws Exception {
        String seedSql = Files.readString(Path.of("db/V2__seed.sql"));
        int failureBlockStart = seedSql.indexOf(INTENTIONAL_FAILURE_MARKER);
        return failureBlockStart >= 0 ? seedSql.substring(0, failureBlockStart) : seedSql;
    }
}
