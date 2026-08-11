package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.entity.Taxpayer;
import com.uptimecrew.tax_liability.exception.TaxLiabilityException;
import com.uptimecrew.tax_liability.model.TaxBracket;
import com.uptimecrew.tax_liability.repository.TaxpayerRepository;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Objects;
import java.util.Optional;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Resolves a taxable amount's bracket by delegating to an injected {@link BracketResolver}
 * strategy, then records the taxpayer under the resolved bracket's jurisdiction via the
 * injected {@link TaxpayerRepository}.
 */
// non-final: @Transactional needs Spring to CGLIB-subclass this bean for its AOP proxy.
@Service
public class TaxLiabilityService {

    private static final Logger LOG = LoggerFactory.getLogger(TaxLiabilityService.class);

    private final BracketResolver bracketResolver;
    private final TaxpayerRepository repository;

    public TaxLiabilityService(BracketResolver bracketResolver, TaxpayerRepository repository) {
        this.bracketResolver = Objects.requireNonNull(bracketResolver, "bracketResolver must not be null");
        this.repository = Objects.requireNonNull(repository, "repository must not be null");
    }

    /**
     * @param id the taxpayer's id, must not be null
     * @param displayName the taxpayer's display name, must not be null
     * @param filingStatus the taxpayer's filing status, must not be null
     * @param taxableAmount the amount to resolve a bracket for, must not be null
     * @return the persisted {@link Taxpayer}, recorded under the injected strategy's
     *         resolved bracket jurisdiction
     * @throws IllegalStateException if the injected strategy resolves no bracket for {@code taxableAmount}
     * @throws TaxLiabilityException if the injected strategy fails to resolve a bracket
     */
    @Transactional
    public Taxpayer computeLiability(String id, String displayName, String filingStatus, BigDecimal taxableAmount) {
        Objects.requireNonNull(id, "id must not be null");
        Objects.requireNonNull(displayName, "displayName must not be null");
        Objects.requireNonNull(filingStatus, "filingStatus must not be null");
        Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
        LOG.info("invoking strategy={} for id={}", bracketResolver.getClass().getSimpleName(), id);
        try {
            Optional<TaxBracket> resolved = bracketResolver.resolve(taxableAmount);
            if (resolved.isEmpty()) {
                throw new IllegalStateException("no tax bracket resolved for taxable amount: " + taxableAmount);
            }
            TaxBracket bracket = resolved.orElseThrow();
            Taxpayer entity = new Taxpayer(id, displayName, filingStatus, bracket.jurisdiction(), Instant.now());
            Taxpayer saved = repository.save(entity);
            LOG.info("persisted entity id={}", saved.getId());
            return saved;
        } catch (TaxLiabilityException ex) {
            LOG.warn("strategy failed: {}", ex.getMessage(), ex);
            throw ex;
        }
    }
}
