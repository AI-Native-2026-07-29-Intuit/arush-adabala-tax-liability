package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.exception.TaxLiabilityException;
import com.uptimecrew.tax_liability.model.TaxBracket;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Objects;
import java.util.Optional;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * Computes the tax owed on a taxable amount by delegating bracket lookup to an
 * injected {@link BracketResolver} strategy and applying the resolved bracket's rate.
 */
@Service
public final class TaxLiabilityService {

    private static final Logger LOG = LoggerFactory.getLogger(TaxLiabilityService.class);

    private final BracketResolver bracketResolver;

    public TaxLiabilityService(BracketResolver bracketResolver) {
        this.bracketResolver = Objects.requireNonNull(bracketResolver, "bracketResolver must not be null");
    }

    /**
     * @param taxableAmount the amount to compute liability for, must not be null
     * @return {@code taxableAmount} multiplied by the injected strategy's resolved rate,
     *         scaled to 2 decimal places with {@link RoundingMode#HALF_UP}
     * @throws IllegalStateException if the injected strategy resolves no bracket for {@code taxableAmount}
     * @throws TaxLiabilityException if the injected strategy fails to resolve a bracket
     */
    public BigDecimal computeLiability(BigDecimal taxableAmount) {
        Objects.requireNonNull(taxableAmount, "taxableAmount must not be null");
        LOG.info("invoking strategy={} for input={}", bracketResolver.getClass().getSimpleName(), taxableAmount);
        try {
            Optional<TaxBracket> resolved = bracketResolver.resolve(taxableAmount);
            if (resolved.isEmpty()) {
                throw new IllegalStateException("no tax bracket resolved for taxable amount: " + taxableAmount);
            }
            BigDecimal liability = taxableAmount.multiply(resolved.orElseThrow().rate()).setScale(2, RoundingMode.HALF_UP);
            LOG.info("strategy returned result={}", liability);
            return liability;
        } catch (TaxLiabilityException ex) {
            LOG.warn("strategy failed: {}", ex.getMessage(), ex);
            throw ex;
        }
    }
}
