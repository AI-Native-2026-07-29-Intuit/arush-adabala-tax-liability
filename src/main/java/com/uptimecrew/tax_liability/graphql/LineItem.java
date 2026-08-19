package com.uptimecrew.tax_liability.graphql;

import java.util.Objects;

/**
 * GraphQL projection of a single {@link com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel
 * .EmbeddedLiability}, returned from {@code Taxpayer.lines} (W3 D4). A record so graphql-java's
 * default {@code PropertyDataFetcher} resolves each schema field straight off the accessor
 * methods without any extra {@code @SchemaMapping} wiring.
 */
public record LineItem(String id, String description, Double amount) {

    public LineItem {
        Objects.requireNonNull(id, "id must not be null");
        Objects.requireNonNull(description, "description must not be null");
        Objects.requireNonNull(amount, "amount must not be null");
    }
}
