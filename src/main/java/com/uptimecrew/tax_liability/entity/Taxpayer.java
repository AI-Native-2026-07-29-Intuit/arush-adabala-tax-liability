package com.uptimecrew.tax_liability.entity;

import jakarta.persistence.CascadeType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.OneToMany;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * Maps to {@code taxcalc.taxpayer} (W2 D1): one row per taxpayer / filing identity.
 */
@Entity
@Table(schema = "taxcalc", name = "taxpayer")
public class Taxpayer {

    @Id
    @Column(length = 64)
    private String id;

    @Column(name = "display_name", nullable = false)
    private String displayName;

    @Column(name = "filing_status", nullable = false)
    private String filingStatus;

    @Column(name = "home_jurisdiction", nullable = false)
    private String homeJurisdiction;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @OneToMany(mappedBy = "taxpayer", fetch = FetchType.LAZY, cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Liability> liabilities = new ArrayList<>();

    protected Taxpayer() {
    }

    public Taxpayer(String id, String displayName, String filingStatus, String homeJurisdiction, Instant createdAt) {
        this.id = Objects.requireNonNull(id, "id must not be null");
        this.displayName = Objects.requireNonNull(displayName, "displayName must not be null");
        this.filingStatus = Objects.requireNonNull(filingStatus, "filingStatus must not be null");
        this.homeJurisdiction = Objects.requireNonNull(homeJurisdiction, "homeJurisdiction must not be null");
        this.createdAt = Objects.requireNonNull(createdAt, "createdAt must not be null");
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

    public List<Liability> getLiabilities() {
        return liabilities;
    }

    @Override
    public boolean equals(Object o) {
        return o instanceof Taxpayer other && Objects.equals(id, other.id);
    }

    @Override
    public int hashCode() {
        return Objects.hashCode(id);
    }

    @Override
    public String toString() {
        return "Taxpayer{id=" + id + ", displayName=" + displayName + ", filingStatus=" + filingStatus
                + ", homeJurisdiction=" + homeJurisdiction + ", createdAt=" + createdAt + "}";
    }
}
