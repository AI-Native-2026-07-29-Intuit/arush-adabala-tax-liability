package com.uptimecrew.tax_liability.lambda;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;

import software.amazon.awssdk.services.dynamodb.model.AttributeValue;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Unit tests for {@link TaxpayerRecord}'s DynamoDB mapping and money discipline (W5 D4).
 *
 * <p>The point of these is the scale-2 / {@code HALF_UP} guarantee surviving the DynamoDB round
 * trip: numbers cross the wire as strings, so the mapper is the one place a stray {@code double}
 * would silently corrupt a tax figure.
 */
class TaxpayerRecordTest {

    private static AttributeValue s(String value) {
        return AttributeValue.builder().s(value).build();
    }

    private static AttributeValue n(String value) {
        return AttributeValue.builder().n(value).build();
    }

    private static Map<String, AttributeValue> validItem() {
        return Map.of(
                "id", s("txp_synth_001"),
                "displayName", s("Synthetic Taxpayer One"),
                "filingStatus", s("SINGLE"),
                "homeJurisdiction", s("CA"),
                "createdAt", s("2026-01-15T10:30:00Z"),
                "liabilities", AttributeValue.builder().l(List.of(
                        AttributeValue.builder().m(Map.of(
                                "taxYear", n("2025"),
                                "bracketId", s("brk-fed-2025-22"),
                                "taxableAmount", n("85000.005"),
                                "liabilityAmount", n("14235.50"),
                                "computedAt", s("2026-01-20T08:00:00Z"))).build())).build());
    }

    @Test
    void mapsEveryScalarAttributeFromTheItem() {
        TaxpayerRecord record = TaxpayerRecord.fromItem(validItem());

        assertThat(record.getId()).isEqualTo("txp_synth_001");
        assertThat(record.getDisplayName()).isEqualTo("Synthetic Taxpayer One");
        assertThat(record.getFilingStatus()).isEqualTo("SINGLE");
        assertThat(record.getHomeJurisdiction()).isEqualTo("CA");
        assertThat(record.getCreatedAt()).isEqualTo(Instant.parse("2026-01-15T10:30:00Z"));
        assertThat(record.getLiabilities()).hasSize(1);
    }

    @Test
    void normalizesMoneyToScaleTwoHalfUp() {
        TaxpayerRecord record = TaxpayerRecord.fromItem(validItem());
        TaxpayerRecord.LiabilityLine line = record.getLiabilities().get(0);

        // 85000.005 stored at scale 3 -> HALF_UP to 85000.01, not truncated to 85000.00.
        assertThat(line.getTaxableAmount()).isEqualTo(new BigDecimal("85000.01"));
        assertThat(line.getLiabilityAmount()).isEqualTo(new BigDecimal("14235.50"));
    }

    @Test
    void totalsLiabilityAtScaleTwoEvenWithNoLines() {
        Map<String, AttributeValue> noLiabilities = Map.of(
                "id", s("txp_synth_002"),
                "displayName", s("Synthetic Taxpayer Two"),
                "filingStatus", s("MARRIED_JOINT"),
                "homeJurisdiction", s("TX"),
                "createdAt", s("2026-02-01T00:00:00Z"));

        TaxpayerRecord record = TaxpayerRecord.fromItem(noLiabilities);

        assertThat(record.getLiabilities()).isEmpty();
        // BigDecimal.ZERO has scale 0; the record must still serialise this as 0.00.
        assertThat(record.getTotalLiability()).isEqualTo(new BigDecimal("0.00"));
    }

    @Test
    void sumsLiabilityLinesIntoTheTotal() {
        TaxpayerRecord record = TaxpayerRecord.fromItem(validItem());

        assertThat(record.getTotalLiability()).isEqualTo(new BigDecimal("14235.50"));
    }

    @Test
    void rejectsAnItemMissingARequiredAttribute() {
        Map<String, AttributeValue> missingFilingStatus = Map.of(
                "id", s("txp_synth_003"),
                "displayName", s("Synthetic Taxpayer Three"),
                "homeJurisdiction", s("NY"),
                "createdAt", s("2026-02-01T00:00:00Z"));

        assertThatThrownBy(() -> TaxpayerRecord.fromItem(missingFilingStatus))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("filingStatus");
    }

    @Test
    void rejectsANonIsoCreatedAt() {
        Map<String, AttributeValue> badTimestamp = Map.of(
                "id", s("txp_synth_004"),
                "displayName", s("Synthetic Taxpayer Four"),
                "filingStatus", s("SINGLE"),
                "homeJurisdiction", s("WA"),
                "createdAt", s("15/01/2026"));

        assertThatThrownBy(() -> TaxpayerRecord.fromItem(badTimestamp))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("ISO-8601");
    }

    @Test
    void serialisesMoneyAtScaleTwoAndTimestampsAsIso8601() throws Exception {
        // The wire contract, asserted on the actual bytes. Both halves fail silently if the
        // ObjectMapper is misconfigured: without JavaTimeModule + WRITE_DATES_AS_TIMESTAMPS
        // disabled, createdAt serialises as a float epoch, and BigDecimal scale is the whole
        // point of the money rule. Neither shows up as a compile error or a failed HTTP call.
        String json = TaxpayerLookupHandler.newObjectMapper()
                .writeValueAsString(TaxpayerRecord.fromItem(validItem()));

        assertThat(json).contains("\"createdAt\":\"2026-01-15T10:30:00Z\"");
        assertThat(json).contains("\"computedAt\":\"2026-01-20T08:00:00Z\"");
        // Trailing zeros preserved - 14235.5 would mean the scale was lost somewhere.
        assertThat(json).contains("\"liabilityAmount\":14235.50");
        assertThat(json).contains("\"totalLiability\":14235.50");
        // 85000.005 rounded HALF_UP at mapping time, then serialised at scale 2.
        assertThat(json).contains("\"taxableAmount\":85000.01");
    }

    @Test
    void rejectsANegativeLiabilityAmount() {
        assertThatThrownBy(() -> new TaxpayerRecord.LiabilityLine(
                2025, "brk-fed-2025-22",
                new BigDecimal("100.00"), new BigDecimal("-1.00"),
                Instant.parse("2026-01-20T08:00:00Z")))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("liabilityAmount");
    }
}
