package com.uptimecrew.tax_liability.repository;

import com.uptimecrew.tax_liability.entity.Bracket;

import java.util.List;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

@Repository
public interface BracketRepository extends JpaRepository<Bracket, String> {

    // Derived query - Spring Data generates the JPQL from the method name.
    List<Bracket> findByJurisdictionAndTaxYearOrderByFloorAmountAsc(String jurisdiction, Integer taxYear);

    // Explicit JPQL - the "unbounded top bracket" concept (ceiling is null) isn't
    // expressible via a plain findBy... derived name.
    @Query("SELECT b FROM Bracket b WHERE b.jurisdiction = :jurisdiction AND b.taxYear = :taxYear "
            + "AND b.ceilingAmount IS NULL")
    List<Bracket> findUnboundedTopBracket(@Param("jurisdiction") String jurisdiction, @Param("taxYear") Integer taxYear);
}
