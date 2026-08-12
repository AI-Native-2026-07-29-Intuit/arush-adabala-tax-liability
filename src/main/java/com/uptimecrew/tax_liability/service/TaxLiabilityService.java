package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.entity.Liability;
import com.uptimecrew.tax_liability.entity.Taxpayer;
import com.uptimecrew.tax_liability.exception.TaxLiabilityException;
import com.uptimecrew.tax_liability.model.TaxBracket;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;
import com.uptimecrew.tax_liability.repository.TaxpayerRepository;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.stream.Collectors;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Resolves a taxable amount's bracket by delegating to an injected {@link BracketResolver}
 * strategy, then records the taxpayer under the resolved bracket's jurisdiction via the
 * injected {@link TaxpayerRepository}. Every write is also projected write-through into
 * MongoDB via the injected {@link TaxpayerReadModelRepository} (W2 D5), and {@link #findById}
 * serves reads from a Redis-backed cache in front of that Mongo read model.
 */
// non-final: @Transactional needs Spring to CGLIB-subclass this bean for its AOP proxy.
@Service
public class TaxLiabilityService {

    static final String CACHE_NAME = "taxcalc.byId";

    private static final Logger LOG = LoggerFactory.getLogger(TaxLiabilityService.class);

    private final BracketResolver bracketResolver;
    private final TaxpayerRepository repository;
    private final TaxpayerReadModelRepository readModelRepository;

    public TaxLiabilityService(BracketResolver bracketResolver, TaxpayerRepository repository,
            TaxpayerReadModelRepository readModelRepository) {
        this.bracketResolver = Objects.requireNonNull(bracketResolver, "bracketResolver must not be null");
        this.repository = Objects.requireNonNull(repository, "repository must not be null");
        this.readModelRepository = Objects.requireNonNull(readModelRepository, "readModelRepository must not be null");
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

            // Write-through: project the JPA entity into a Mongo read model.
            readModelRepository.save(toReadModel(saved));
            LOG.info("write-through to mongo id={} filingStatus={}", saved.getId(), saved.getFilingStatus());

            return saved;
        } catch (TaxLiabilityException ex) {
            LOG.warn("strategy failed: {}", ex.getMessage(), ex);
            throw ex;
        }
    }

    /**
     * Read path: Redis (via {@code @Cacheable}) -&gt; Mongo -&gt; Postgres.
     *
     * @param id the taxpayer's id, must not be null
     * @return the taxpayer's read model, or an empty {@link Optional} if no taxpayer with that
     *         id exists in either the Mongo read model or the Postgres source of truth
     */
    @Cacheable(value = CACHE_NAME, unless = "#result == null")
    public Optional<TaxpayerReadModel> findById(String id) {
        Objects.requireNonNull(id, "id must not be null");
        LOG.info("cache miss on id={}; reading from mongo", id);
        Optional<TaxpayerReadModel> fromMongo = readModelRepository.findById(id);
        if (fromMongo.isPresent()) {
            return fromMongo;
        }
        // Fallback: rebuild a read-model projection from the JPA entity. Optional but
        // recommended so a Mongo wipe doesn't break the read path.
        return repository.findById(id).map(this::toReadModel);
    }

    private TaxpayerReadModel toReadModel(Taxpayer taxpayer) {
        List<TaxpayerReadModel.EmbeddedLiability> liabilities = taxpayer.getLiabilities().stream()
                .map(this::toEmbeddedLiability)
                .collect(Collectors.toList());
        return new TaxpayerReadModel(taxpayer.getId(), taxpayer.getDisplayName(), taxpayer.getFilingStatus(),
                taxpayer.getHomeJurisdiction(), taxpayer.getCreatedAt(), liabilities);
    }

    private TaxpayerReadModel.EmbeddedLiability toEmbeddedLiability(Liability liability) {
        return new TaxpayerReadModel.EmbeddedLiability(liability.getTaxYear(), liability.getBracket().getId(),
                liability.getTaxableAmount(), liability.getLiabilityAmount(), liability.getComputedAt());
    }
}
