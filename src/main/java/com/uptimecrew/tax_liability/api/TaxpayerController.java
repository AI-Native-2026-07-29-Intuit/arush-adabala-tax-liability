package com.uptimecrew.tax_liability.api;

import java.util.Map;
import java.util.Objects;
import java.util.Optional;

import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.service.TaxLiabilityService;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Read-only edge for {@link TaxLiabilityService#findById}, gated by the {@link
 * com.uptimecrew.tax_liability.security.SecurityConfig} filter chain: every route here requires a
 * Bearer JWT carrying both the {@code taxpayers.read} scope and the {@code TAXPAYER_READER} role
 * (see the {@code @PreAuthorize} SpEL below). {@link #summary} additionally sits behind {@link
 * com.uptimecrew.tax_liability.security.RateLimitFilter}, since it stubs an LLM call that will
 * cost real money once Week 5 wires up a real model.
 */
@RestController
@RequestMapping("/api/taxpayers")
public class TaxpayerController {

    private static final Logger LOG = LoggerFactory.getLogger(TaxpayerController.class);
    private static final String READ_AUTHORITY =
            "hasAuthority('SCOPE_taxpayers.read') and hasRole('TAXPAYER_READER')";

    private final TaxLiabilityService service;

    public TaxpayerController(TaxLiabilityService service) {
        this.service = Objects.requireNonNull(service, "service must not be null");
    }

    @GetMapping("/{id}")
    @PreAuthorize(READ_AUTHORITY)
    public ResponseEntity<TaxpayerReadModel> getById(@PathVariable String id, @AuthenticationPrincipal Jwt jwt) {
        LOG.info("get id={} subject={}", id, jwt.getSubject());
        Optional<TaxpayerReadModel> found = service.findById(id);
        return found.map(ResponseEntity::ok).orElseGet(() -> ResponseEntity.notFound().build());
    }

    @GetMapping("/{id}/summary")
    @PreAuthorize(READ_AUTHORITY)
    public Map<String, String> summary(@PathVariable String id, @AuthenticationPrincipal Jwt jwt)
            throws InterruptedException {
        LOG.info("summary id={} subject={}", id, jwt.getSubject());
        Thread.sleep(100); // (1) stub LLM latency; Week 5 replaces this with a real call
        return Map.of("summary", "Stub LLM summary for " + id);
    }
}
