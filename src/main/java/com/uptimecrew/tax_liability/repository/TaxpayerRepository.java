package com.uptimecrew.tax_liability.repository;

import com.uptimecrew.tax_liability.entity.Taxpayer;

import java.util.List;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

@Repository
public interface TaxpayerRepository extends JpaRepository<Taxpayer, String> {

    // Derived query - Spring Data generates the JPQL from the method name.
    List<Taxpayer> findByFilingStatus(String filingStatus);

    // Explicit JPQL - use when the derived name would get ugly.
    // Returns taxpayers that have at least minLiabilities recorded liabilities.
    @Query("SELECT t FROM Taxpayer t JOIN t.liabilities l GROUP BY t HAVING COUNT(l) >= :minLiabilities")
    List<Taxpayer> findWithAtLeastNLiabilities(@Param("minLiabilities") long minLiabilities);
}
