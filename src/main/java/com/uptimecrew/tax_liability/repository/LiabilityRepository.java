package com.uptimecrew.tax_liability.repository;

import com.uptimecrew.tax_liability.entity.Liability;
import com.uptimecrew.tax_liability.entity.LiabilityId;

import java.math.BigDecimal;
import java.util.List;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

@Repository
public interface LiabilityRepository extends JpaRepository<Liability, LiabilityId> {

    // Derived query - Spring Data generates the JPQL from the method name.
    List<Liability> findByTaxpayerId(String taxpayerId);

    // Explicit JPQL - an aggregate (total liability per tax year) isn't
    // expressible via a plain findBy... derived name.
    @Query("SELECT COALESCE(SUM(l.liabilityAmount), 0) FROM Liability l WHERE l.taxYear = :taxYear")
    BigDecimal sumLiabilityAmountForTaxYear(@Param("taxYear") Integer taxYear);
}
