package com.uptimecrew.tax_liability.service;

import com.uptimecrew.tax_liability.model.TaxBracket;

import java.math.BigDecimal;
import java.util.Collection;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.stream.Collectors;

/**
 * Immutable, queryable store of {@link TaxBracket} records keyed by id. The registry
 * takes a defensive, unmodifiable copy of the brackets it is given at construction time,
 * so callers can neither mutate the source collection after the fact nor reach into the
 * registry's internal state — every accessor here returns either a value type or a fresh
 * unmodifiable {@link List}.
 */
public final class BracketRegistry {

    private final Map<String, TaxBracket> bracketsById;

    /**
     * @param brackets the brackets to register; copied defensively, must not be null
     */
    public BracketRegistry(Collection<TaxBracket> brackets) {
        Objects.requireNonNull(brackets, "brackets must not be null");
        this.bracketsById = brackets.stream()
                .collect(Collectors.toUnmodifiableMap(TaxBracket::id, bracket -> bracket));
    }

    /**
     * @return the number of brackets held by this registry
     */
    public int size() {
        return bracketsById.size();
    }

    /**
     * @param id the bracket id to look up, must not be null
     * @return the matching {@link TaxBracket}, or {@link Optional#empty()} if no bracket
     *         with that id is registered
     */
    public Optional<TaxBracket> findById(String id) {
        Objects.requireNonNull(id, "id must not be null");
        return Optional.ofNullable(bracketsById.get(id));
    }

    /**
     * @param jurisdiction the jurisdiction to match exactly, must not be null
     * @param floor the inclusive lower bound on a bracket's floor, must not be null
     * @return brackets in {@code jurisdiction} whose floor is at or above {@code floor},
     *         sorted by floor ascending; an empty, unmodifiable list if none match
     */
    public List<TaxBracket> findByJurisdictionAbove(String jurisdiction, BigDecimal floor) {
        Objects.requireNonNull(jurisdiction, "jurisdiction must not be null");
        Objects.requireNonNull(floor, "floor must not be null");
        return bracketsById.values().stream()
                .filter(bracket -> bracket.jurisdiction().equals(jurisdiction) && bracket.floor().compareTo(floor) >= 0)
                .sorted(Comparator.comparing(TaxBracket::floor))
                .toList();
    }
}
