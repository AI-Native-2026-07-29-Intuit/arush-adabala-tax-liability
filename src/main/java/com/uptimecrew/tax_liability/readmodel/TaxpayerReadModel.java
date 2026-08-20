package com.uptimecrew.tax_liability.readmodel;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Objects;

import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.index.Indexed;
import org.springframework.data.mongodb.core.mapping.Document;

/**
 * Denormalized MongoDB read model mirroring {@link com.uptimecrew.tax_liability.entity.Taxpayer}
 * (W2 D4): where the JPA entity lazily {@code @OneToMany}-joins its liabilities out of
 * {@code taxcalc.liability}, this document embeds them inline so a single lookup-by-id returns
 * the whole tree instead of a Postgres round-trip against the child table. The {@code id} is the
 * same value as the JPA entity's, so a Mongo lookup and a Postgres lookup return the same
 * logical row.
 *
 * <p>Implements {@link Serializable} (as does {@link EmbeddedLiability}) because this document is
 * also the value type cached behind {@code @Cacheable} on
 * {@link com.uptimecrew.tax_liability.service.TaxLiabilityService}: Spring's default Redis cache
 * configuration serializes cached values with the JDK serializer, and the first cache miss would
 * otherwise fail with a {@link java.io.NotSerializableException}.
 *
 * <p>{@link #getTags()} (W3 D5) backs the GraphQL {@code Taxpayer.tags} field and the
 * {@code taxpayersByTag} query - the small feature shipped that day through a 3-agent
 * generator/tester/reviewer workflow.
 */
@Document(collection = "taxpayers")
public class TaxpayerReadModel implements Serializable {

    private static final long serialVersionUID = 1L;

    @Id
    private String id;

    private String displayName;

    @Indexed
    private String filingStatus;

    private String homeJurisdiction;

    private Instant createdAt;

    private List<EmbeddedLiability> liabilities;

    // Defaulted (not left null): Spring Data Mongo populates fields via reflection off the
    // no-arg constructor below, not the parameterized one, so a pre-existing document written
    // before this field existed has no "tags" key in its BSON and this initializer is the only
    // thing standing between that and a null tags - which the non-null GraphQL `tags: [String!]!`
    // field would then reject with a resolution error.
    private List<String> tags = List.of();

    /** Required by Spring Data Mongo. */
    public TaxpayerReadModel() {
    }

    public TaxpayerReadModel(String id, String displayName, String filingStatus, String homeJurisdiction,
            Instant createdAt, List<EmbeddedLiability> liabilities) {
        this(id, displayName, filingStatus, homeJurisdiction, createdAt, liabilities, List.of());
    }

    public TaxpayerReadModel(String id, String displayName, String filingStatus, String homeJurisdiction,
            Instant createdAt, List<EmbeddedLiability> liabilities, List<String> tags) {
        this.id = Objects.requireNonNull(id, "id must not be null");
        this.displayName = Objects.requireNonNull(displayName, "displayName must not be null");
        this.filingStatus = Objects.requireNonNull(filingStatus, "filingStatus must not be null");
        this.homeJurisdiction = Objects.requireNonNull(homeJurisdiction, "homeJurisdiction must not be null");
        this.createdAt = Objects.requireNonNull(createdAt, "createdAt must not be null");
        this.liabilities = Objects.requireNonNull(liabilities, "liabilities must not be null");
        this.tags = Objects.requireNonNull(tags, "tags must not be null");
        if (id.isBlank()) {
            throw new IllegalArgumentException("id must not be blank");
        }
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

    public List<EmbeddedLiability> getLiabilities() {
        return liabilities;
    }

    public List<String> getTags() {
        return tags;
    }

    /**
     * Re-projects this document from a {@code taxpayers.events} update (W3 D3): overwrites the
     * scalar fields with the event's values. Applying the same event twice produces the same
     * document, so at-least-once Kafka redelivery is safe.
     */
    public void applyEvent(String displayName, String filingStatus, String homeJurisdiction, Instant createdAt) {
        this.displayName = Objects.requireNonNull(displayName, "displayName must not be null");
        this.filingStatus = Objects.requireNonNull(filingStatus, "filingStatus must not be null");
        this.homeJurisdiction = Objects.requireNonNull(homeJurisdiction, "homeJurisdiction must not be null");
        this.createdAt = Objects.requireNonNull(createdAt, "createdAt must not be null");
    }

    @Override
    public boolean equals(Object o) {
        return o instanceof TaxpayerReadModel other && Objects.equals(id, other.id);
    }

    @Override
    public int hashCode() {
        return Objects.hashCode(id);
    }

    @Override
    public String toString() {
        return "TaxpayerReadModel{id=" + id + ", displayName=" + displayName + ", filingStatus=" + filingStatus
                + ", homeJurisdiction=" + homeJurisdiction + ", createdAt=" + createdAt
                + ", liabilities=" + liabilities + ", tags=" + tags + "}";
    }

    /**
     * Embedded projection of {@link com.uptimecrew.tax_liability.entity.Liability}: the child
     * data the JPA side would {@code JOIN FETCH}, denormalized inline instead.
     */
    public static final class EmbeddedLiability implements Serializable {

        private static final long serialVersionUID = 1L;

        private Integer taxYear;

        private String bracketId;

        private BigDecimal taxableAmount;

        private BigDecimal liabilityAmount;

        private Instant computedAt;

        /** Required by Spring Data Mongo. */
        public EmbeddedLiability() {
        }

        public EmbeddedLiability(Integer taxYear, String bracketId, BigDecimal taxableAmount,
                BigDecimal liabilityAmount, Instant computedAt) {
            this.taxYear = Objects.requireNonNull(taxYear, "taxYear must not be null");
            this.bracketId = Objects.requireNonNull(bracketId, "bracketId must not be null");
            this.taxableAmount = Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
            this.liabilityAmount = Objects.requireNonNull(liabilityAmount, "liabilityAmount must not be null");
            this.computedAt = Objects.requireNonNull(computedAt, "computedAt must not be null");
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

        // Unlike Taxpayer/Bracket/Liability, this is a value object embedded inline rather than
        // an entity with its own id, so equality is on every field: (taxYear, bracketId) alone
        // would wrongly equate two liabilities in the same year and bracket that differ in
        // amount (e.g. before/after a recomputation).
        @Override
        public boolean equals(Object o) {
            return o instanceof EmbeddedLiability other
                    && Objects.equals(taxYear, other.taxYear)
                    && Objects.equals(bracketId, other.bracketId)
                    && Objects.equals(taxableAmount, other.taxableAmount)
                    && Objects.equals(liabilityAmount, other.liabilityAmount)
                    && Objects.equals(computedAt, other.computedAt);
        }

        @Override
        public int hashCode() {
            return Objects.hash(taxYear, bracketId, taxableAmount, liabilityAmount, computedAt);
        }

        @Override
        public String toString() {
            return "EmbeddedLiability{taxYear=" + taxYear + ", bracketId=" + bracketId
                    + ", taxableAmount=" + taxableAmount + ", liabilityAmount=" + liabilityAmount
                    + ", computedAt=" + computedAt + "}";
        }
    }
}
