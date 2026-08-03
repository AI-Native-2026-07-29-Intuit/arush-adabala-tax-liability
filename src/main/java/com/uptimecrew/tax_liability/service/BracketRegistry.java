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

public final class BracketRegistry {

    private final Map<String, TaxBracket> bracketsById;

    public BracketRegistry(Collection<TaxBracket> brackets) {
        Objects.requireNonNull(brackets, "brackets must not be null");
        this.bracketsById = brackets.stream()
                .collect(Collectors.toUnmodifiableMap(TaxBracket::getId, bracket -> bracket));
    }

    public int size() {
        return bracketsById.size();
    }

    public Optional<TaxBracket> findById(String id) {
        Objects.requireNonNull(id, "id must not be null");
        return Optional.ofNullable(bracketsById.get(id));
    }

    public List<TaxBracket> findByJurisdictionAbove(String jurisdiction, BigDecimal floor) {
        Objects.requireNonNull(jurisdiction, "jurisdiction must not be null");
        Objects.requireNonNull(floor, "floor must not be null");
        return bracketsById.values().stream()
                .filter(bracket -> bracket.getJurisdiction().equals(jurisdiction) && bracket.getFloor().compareTo(floor) >= 0)
                .sorted(Comparator.comparing(TaxBracket::getFloor))
                .toList();
    }
}
