package com.uptimecrew.tax_liability.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

import com.uptimecrew.tax_liability.consumer.TaxpayerUpdatedEvent;
import com.uptimecrew.tax_liability.entity.Liability;
import com.uptimecrew.tax_liability.entity.Taxpayer;
import com.uptimecrew.tax_liability.exception.TaxLiabilityException;
import com.uptimecrew.tax_liability.graphql.LineItem;
import com.uptimecrew.tax_liability.model.TaxBracket;
import com.uptimecrew.tax_liability.outbox.EventOutboxEntity;
import com.uptimecrew.tax_liability.outbox.EventOutboxRepository;
import com.uptimecrew.tax_liability.outbox.OutboxTopics;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;
import com.uptimecrew.tax_liability.repository.TaxpayerRepository;

import java.io.UncheckedIOException;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.function.Function;
import java.util.stream.Collectors;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
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

    public static final String CACHE_NAME = "taxcalc.byId";

    private static final Logger LOG = LoggerFactory.getLogger(TaxLiabilityService.class);

    private final BracketResolver bracketResolver;
    private final TaxpayerRepository repository;
    private final TaxpayerReadModelRepository readModelRepository;
    private final EventOutboxRepository outboxRepository;
    private final ObjectMapper objectMapper;

    public TaxLiabilityService(BracketResolver bracketResolver, TaxpayerRepository repository,
            TaxpayerReadModelRepository readModelRepository, EventOutboxRepository outboxRepository,
            ObjectMapper objectMapper) {
        this.bracketResolver = Objects.requireNonNull(bracketResolver, "bracketResolver must not be null");
        this.repository = Objects.requireNonNull(repository, "repository must not be null");
        this.readModelRepository = Objects.requireNonNull(readModelRepository, "readModelRepository must not be null");
        this.outboxRepository = Objects.requireNonNull(outboxRepository, "outboxRepository must not be null");
        this.objectMapper = Objects.requireNonNull(objectMapper, "objectMapper must not be null");
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

            // Transactional outbox (W3 D3): the outbox row is inserted in the SAME Postgres
            // transaction as the domain write above, so the two can never diverge - either both
            // commit or both roll back. OutboxPublisher asynchronously sweeps this row to Kafka.
            outboxRepository.save(new EventOutboxEntity(saved.getId(), OutboxTopics.TAXPAYER_EVENTS,
                    serializeEvent(toUpdatedEvent(saved))));

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
    // (readOnly = true): the Postgres fallback below reads Taxpayer.liabilities, a LAZY
    // @OneToMany - with open-in-view off, that access needs an active Hibernate session of its
    // own (toReadModel is called from inside this method, not from a web-request-scoped one).
    @Transactional(readOnly = true)
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

    /**
     * Powers the GraphQL {@code latestTaxpayers} query (W3 D4 Task 1).
     *
     * @param limit the maximum number of taxpayers to return, must be positive
     * @return up to {@code limit} taxpayers from the Mongo read model, newest {@code createdAt} first
     * @throws IllegalArgumentException if {@code limit} is not positive
     */
    public List<TaxpayerReadModel> findLatest(int limit) {
        if (limit <= 0) {
            throw new IllegalArgumentException("limit must be positive: " + limit);
        }
        return readModelRepository
                .findAll(PageRequest.of(0, limit, Sort.by(Sort.Direction.DESC, "createdAt")))
                .getContent();
    }

    /**
     * Powers the GraphQL {@code taxpayersByTag} query (W3 D5).
     *
     * @param tag the tag to filter taxpayers by, must not be null
     * @return every taxpayer from the Mongo read model whose {@code tags} contain {@code tag}
     */
    public List<TaxpayerReadModel> findByTag(String tag) {
        Objects.requireNonNull(tag, "tag must not be null");
        return readModelRepository.findByTagsContaining(tag);
    }

    /**
     * Batch resolver for the GraphQL {@code Taxpayer.lines} field (W3 D4 Task 2, the N+1 fix):
     * {@link TaxpayerReadModel} already embeds its liabilities inline (the W2 D5 read-model
     * denormalization), so grouping the batch costs no extra Mongo round-trip at all -
     * {@code @BatchMapping} on the caller still collapses what would otherwise be one
     * per-taxpayer field resolution into a single call regardless of how many taxpayers are in
     * the result set.
     *
     * @param parents the taxpayers whose line items are being resolved in this GraphQL response
     * @return each parent mapped to its liabilities projected as {@link LineItem}s
     */
    public Map<TaxpayerReadModel, List<LineItem>> loadLineItemsByParent(List<TaxpayerReadModel> parents) {
        Objects.requireNonNull(parents, "parents must not be null");
        return parents.stream().collect(Collectors.toMap(Function.identity(), this::toLineItems));
    }

    private List<LineItem> toLineItems(TaxpayerReadModel parent) {
        return parent.getLiabilities().stream()
                .map(liability -> new LineItem(parent.getId() + "-" + liability.getTaxYear(),
                        "Tax year " + liability.getTaxYear() + " liability (" + liability.getBracketId() + ")",
                        liability.getLiabilityAmount().doubleValue()))
                .collect(Collectors.toList());
    }

    private TaxpayerUpdatedEvent toUpdatedEvent(Taxpayer taxpayer) {
        return new TaxpayerUpdatedEvent(taxpayer.getId(), taxpayer.getDisplayName(), taxpayer.getFilingStatus(),
                taxpayer.getHomeJurisdiction(), taxpayer.getCreatedAt());
    }

    private String serializeEvent(TaxpayerUpdatedEvent event) {
        try {
            return objectMapper.writeValueAsString(event);
        } catch (JsonProcessingException ex) {
            // A record of Strings and an Instant is always serializable; this only guards
            // against a future field change that breaks that assumption.
            throw new UncheckedIOException("failed to serialize outbox event for aggregateId: " + event.aggregateId(), ex);
        }
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
