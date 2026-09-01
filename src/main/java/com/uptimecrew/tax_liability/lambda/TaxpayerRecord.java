package com.uptimecrew.tax_liability.lambda;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Objects;

import software.amazon.awssdk.services.dynamodb.model.AttributeValue;

/**
 * The read-side projection of a taxpayer as it is stored in the {@code taxpayers-${StageName}}
 * DynamoDB table and returned by {@link TaxpayerLookupHandler} (W5 D4).
 *
 * <p>This is the same logical row as the service's
 * {@code com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel} and, further upstream, the JPA
 * {@code entity.Taxpayer} - the deployment shape changed for this deliverable, the domain did not.
 * It is declared separately rather than reused because that class is a Spring Data
 * {@code @Document} and this Lambda's dependency closure deliberately contains no Spring.
 *
 * <p>Immutable value type. Money is {@link BigDecimal} normalized to scale 2 / {@code HALF_UP};
 * the id is a {@link String}; {@code createdAt} is an {@link Instant}. Jackson serialises it via
 * the {@code getX()} accessors, so the wire contract is exactly the accessor set below.
 */
public final class TaxpayerRecord {

    private final String id;
    private final String displayName;
    private final String filingStatus;
    private final String homeJurisdiction;
    private final Instant createdAt;
    private final List<LiabilityLine> liabilities;
    private final BigDecimal totalLiability;

    /**
     * @param id               taxpayer id, the table's {@code id} partition key; never blank
     * @param displayName      human-readable name; never blank
     * @param filingStatus     filing status code (e.g. {@code SINGLE}); never blank
     * @param homeJurisdiction home state/jurisdiction code; never blank
     * @param createdAt        when the taxpayer was first recorded
     * @param liabilities      computed liability lines; may be empty, never null
     * @throws NullPointerException     if any argument is null
     * @throws IllegalArgumentException if any string argument is blank
     */
    public TaxpayerRecord(String id, String displayName, String filingStatus, String homeJurisdiction,
            Instant createdAt, List<LiabilityLine> liabilities) {
        Objects.requireNonNull(id, "id must not be null");
        Objects.requireNonNull(displayName, "displayName must not be null");
        Objects.requireNonNull(filingStatus, "filingStatus must not be null");
        Objects.requireNonNull(homeJurisdiction, "homeJurisdiction must not be null");
        Objects.requireNonNull(createdAt, "createdAt must not be null");
        Objects.requireNonNull(liabilities, "liabilities must not be null");
        if (id.isBlank()) {
            throw new IllegalArgumentException("id must not be blank");
        }
        if (displayName.isBlank()) {
            throw new IllegalArgumentException("displayName must not be blank");
        }
        if (filingStatus.isBlank()) {
            throw new IllegalArgumentException("filingStatus must not be blank");
        }
        if (homeJurisdiction.isBlank()) {
            throw new IllegalArgumentException("homeJurisdiction must not be blank");
        }
        this.id = id;
        this.displayName = displayName;
        this.filingStatus = filingStatus;
        this.homeJurisdiction = homeJurisdiction;
        this.createdAt = createdAt;
        this.liabilities = List.copyOf(liabilities);

        BigDecimal running = BigDecimal.ZERO;
        for (LiabilityLine line : this.liabilities) {
            running = running.add(line.getLiabilityAmount());
        }
        // Summed from already-scale-2 addends, so this setScale never actually rounds - it only
        // guarantees the zero case (BigDecimal.ZERO has scale 0) serialises as 0.00 like the rest.
        this.totalLiability = running.setScale(2, RoundingMode.HALF_UP);
    }

    /**
     * Maps a raw DynamoDB {@code GetItem} result into a {@code TaxpayerRecord}.
     *
     * <p>Hand-mapped rather than routed through the DynamoDB Enhanced Client on purpose: the
     * enhanced client builds an annotation-driven {@code TableSchema} at construction time, which
     * is INIT-phase work this one-table, one-key read does not need.
     *
     * @param item the attribute map returned by {@code GetItemResponse.item()}; never null
     * @return the mapped record
     * @throws NullPointerException     if {@code item} is null
     * @throws IllegalArgumentException if a required attribute is missing or holds the wrong type
     */
    public static TaxpayerRecord fromItem(Map<String, AttributeValue> item) {
        Objects.requireNonNull(item, "item must not be null");
        List<LiabilityLine> lines = new ArrayList<>();
        AttributeValue rawLiabilities = item.get("liabilities");
        if (rawLiabilities != null && rawLiabilities.hasL()) {
            for (AttributeValue element : rawLiabilities.l()) {
                if (!element.hasM()) {
                    throw new IllegalArgumentException("liabilities elements must be maps");
                }
                lines.add(LiabilityLine.fromItem(element.m()));
            }
        }
        return new TaxpayerRecord(
                requiredString(item, "id"),
                requiredString(item, "displayName"),
                requiredString(item, "filingStatus"),
                requiredString(item, "homeJurisdiction"),
                requiredInstant(item, "createdAt"),
                lines);
    }

    public String getId() {
        return id;
    }

    public String getDisplayName() {
        return displayName;
    }

    public String getFilingStatus() {
        return filingStatus;
    }

    public String getHomeJurisdiction() {
        return homeJurisdiction;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    /** @return an unmodifiable view; the record is immutable. */
    public List<LiabilityLine> getLiabilities() {
        return Collections.unmodifiableList(liabilities);
    }

    /** @return the sum of every line's {@code liabilityAmount}, at scale 2. */
    public BigDecimal getTotalLiability() {
        return totalLiability;
    }

    /** Identity equality on {@link #getId()}, matching the JPA/Mongo sides of the same row. */
    @Override
    public boolean equals(Object o) {
        return o instanceof TaxpayerRecord other && Objects.equals(id, other.id);
    }

    @Override
    public int hashCode() {
        return Objects.hashCode(id);
    }

    @Override
    public String toString() {
        return "TaxpayerRecord{id=" + id + ", displayName=" + displayName
                + ", filingStatus=" + filingStatus + ", homeJurisdiction=" + homeJurisdiction
                + ", createdAt=" + createdAt + ", liabilities=" + liabilities
                + ", totalLiability=" + totalLiability + "}";
    }

    private static String requiredString(Map<String, AttributeValue> item, String key) {
        AttributeValue value = item.get(key);
        if (value == null || value.s() == null) {
            throw new IllegalArgumentException("item is missing required string attribute: " + key);
        }
        return value.s();
    }

    private static Instant requiredInstant(Map<String, AttributeValue> item, String key) {
        String raw = requiredString(item, key);
        try {
            return Instant.parse(raw);
        } catch (java.time.format.DateTimeParseException e) {
            throw new IllegalArgumentException("attribute " + key + " is not an ISO-8601 instant: " + raw, e);
        }
    }

    /**
     * One computed tax liability for a taxpayer in a given year and bracket - the Lambda-side
     * mirror of {@code readmodel.TaxpayerReadModel.EmbeddedLiability}.
     *
     * <p>Immutable value type; both amounts are normalized to scale 2 / {@code HALF_UP}.
     */
    public static final class LiabilityLine {

        private final Integer taxYear;
        private final String bracketId;
        private final BigDecimal taxableAmount;
        private final BigDecimal liabilityAmount;
        private final Instant computedAt;

        /**
         * @param taxYear         the tax year this liability was computed for
         * @param bracketId       the bracket id used; never blank
         * @param taxableAmount   taxable income; never negative
         * @param liabilityAmount tax owed; never negative
         * @param computedAt      when the figure was computed
         * @throws NullPointerException     if any argument is null
         * @throws IllegalArgumentException if {@code bracketId} is blank or an amount is negative
         */
        public LiabilityLine(Integer taxYear, String bracketId, BigDecimal taxableAmount,
                BigDecimal liabilityAmount, Instant computedAt) {
            Objects.requireNonNull(taxYear, "taxYear must not be null");
            Objects.requireNonNull(bracketId, "bracketId must not be null");
            Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
            Objects.requireNonNull(liabilityAmount, "liabilityAmount must not be null");
            Objects.requireNonNull(computedAt, "computedAt must not be null");
            if (bracketId.isBlank()) {
                throw new IllegalArgumentException("bracketId must not be blank");
            }
            if (taxableAmount.signum() < 0) {
                throw new IllegalArgumentException("taxableAmount must not be negative: " + taxableAmount);
            }
            if (liabilityAmount.signum() < 0) {
                throw new IllegalArgumentException("liabilityAmount must not be negative: " + liabilityAmount);
            }
            this.taxYear = taxYear;
            this.bracketId = bracketId;
            this.taxableAmount = taxableAmount.setScale(2, RoundingMode.HALF_UP);
            this.liabilityAmount = liabilityAmount.setScale(2, RoundingMode.HALF_UP);
            this.computedAt = computedAt;
        }

        /**
         * Maps one element of the item's {@code liabilities} list.
         *
         * @param item the nested attribute map; never null
         * @return the mapped line
         * @throws IllegalArgumentException if a required attribute is missing or mistyped
         */
        public static LiabilityLine fromItem(Map<String, AttributeValue> item) {
            Objects.requireNonNull(item, "item must not be null");
            return new LiabilityLine(
                    Integer.valueOf(requiredNumber(item, "taxYear").intValueExact()),
                    requiredString(item, "bracketId"),
                    requiredNumber(item, "taxableAmount"),
                    requiredNumber(item, "liabilityAmount"),
                    requiredInstant(item, "computedAt"));
        }

        public Integer getTaxYear() {
            return taxYear;
        }

        public String getBracketId() {
            return bracketId;
        }

        public BigDecimal getTaxableAmount() {
            return taxableAmount;
        }

        public BigDecimal getLiabilityAmount() {
            return liabilityAmount;
        }

        public Instant getComputedAt() {
            return computedAt;
        }

        // A value object with no id of its own, so equality is on every field: (taxYear,
        // bracketId) alone would wrongly equate two lines that differ only in amount, e.g.
        // before and after a recomputation.
        @Override
        public boolean equals(Object o) {
            return o instanceof LiabilityLine other
                    && Objects.equals(taxYear, other.taxYear)
                    && Objects.equals(bracketId, other.bracketId)
                    && taxableAmount.compareTo(other.taxableAmount) == 0
                    && liabilityAmount.compareTo(other.liabilityAmount) == 0
                    && Objects.equals(computedAt, other.computedAt);
        }

        @Override
        public int hashCode() {
            return Objects.hash(taxYear, bracketId, taxableAmount.stripTrailingZeros(),
                    liabilityAmount.stripTrailingZeros(), computedAt);
        }

        @Override
        public String toString() {
            return "LiabilityLine{taxYear=" + taxYear + ", bracketId=" + bracketId
                    + ", taxableAmount=" + taxableAmount + ", liabilityAmount=" + liabilityAmount
                    + ", computedAt=" + computedAt + "}";
        }

        private static BigDecimal requiredNumber(Map<String, AttributeValue> item, String key) {
            AttributeValue value = item.get(key);
            if (value == null || value.n() == null) {
                throw new IllegalArgumentException("item is missing required numeric attribute: " + key);
            }
            try {
                // DynamoDB stores numbers as strings on the wire, which is precisely why money
                // survives the round trip: new BigDecimal(String) never goes through a double.
                return new BigDecimal(value.n());
            } catch (NumberFormatException e) {
                throw new IllegalArgumentException("attribute " + key + " is not a number: " + value.n(), e);
            }
        }
    }
}
