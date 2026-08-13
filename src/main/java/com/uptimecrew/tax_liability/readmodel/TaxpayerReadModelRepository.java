package com.uptimecrew.tax_liability.readmodel;

import java.util.List;

import org.springframework.data.mongodb.repository.MongoRepository;

/**
 * Manages {@link TaxpayerReadModel} documents in the {@code taxpayers} MongoDB collection.
 */
public interface TaxpayerReadModelRepository extends MongoRepository<TaxpayerReadModel, String> {

    // Derived query - Spring Data generates the Mongo query from the method name.
    List<TaxpayerReadModel> findByFilingStatus(String filingStatus);
}
