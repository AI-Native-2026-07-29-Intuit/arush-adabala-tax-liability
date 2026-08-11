package com.uptimecrew.tax_liability;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;

import com.uptimecrew.tax_liability.entity.Taxpayer;
import com.uptimecrew.tax_liability.service.TaxLiabilityService;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;

@SpringBootTest
@ActiveProfiles("test")
class ApplicationContextLoadIT extends AbstractPostgresIT {

    @Autowired TaxLiabilityService service;

    @Test
    void context_loads_and_service_bean_is_wired() {
        assertThat(service)
                .as("Spring-managed TaxLiabilityService should be wired by the context")
                .isNotNull();
    }

    @Test
    void service_delegates_to_primary_strategy_and_persists_the_taxpayer() {
        Taxpayer saved = service.computeLiability(
                "it-context-load-001", "Ada Lovelace", "SINGLE", new BigDecimal("75000.00"));

        assertThat(saved.getId()).isEqualTo("it-context-load-001");
        assertThat(saved.getHomeJurisdiction()).isEqualTo("FEDERAL");
    }
}
