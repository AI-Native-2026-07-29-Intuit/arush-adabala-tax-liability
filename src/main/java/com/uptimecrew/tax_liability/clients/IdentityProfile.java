package com.uptimecrew.tax_liability.clients;

/** Small read-model record returned by the identity microservice. */
public record IdentityProfile(String id, String displayName, String region) {
}
