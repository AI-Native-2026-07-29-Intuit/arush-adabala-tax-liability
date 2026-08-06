package com.uptimecrew.tax_liability.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.uptimecrew.tax_liability.exception.BracketResolutionFailedException;
import com.uptimecrew.tax_liability.exception.InvalidIncomeException;
import com.uptimecrew.tax_liability.model.TaxBracket;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class ProgressiveStateBracketResolverTest {

    private static final String JURISDICTION = "CALIFORNIA";

    private static final TaxBracket BRACKET_1 = new TaxBracket(
            "ca-bracket-1", JURISDICTION, new BigDecimal("0.01"), new BigDecimal("0"), new BigDecimal("20000"));
    private static final TaxBracket BRACKET_2 = new TaxBracket(
            "ca-bracket-2", JURISDICTION, new BigDecimal("0.02"), new BigDecimal("20000"), new BigDecimal("50000"));
    private static final TaxBracket BRACKET_3 = new TaxBracket(
            "ca-bracket-3", JURISDICTION, new BigDecimal("0.03"), new BigDecimal("50000"), null);

    @Test
    @DisplayName("resolveFor_validInput_returnsExpectedResult")
    void resolveFor_validInput_returnsExpectedResult() {
        // Arrange: a progressive resolver with three ascending CALIFORNIA brackets.
        ProgressiveStateBracketResolver subject = new ProgressiveStateBracketResolver(
                JURISDICTION, List.of(BRACKET_1, BRACKET_2, BRACKET_3));

        // Act: resolve an amount that falls squarely inside the middle bracket.
        Optional<TaxBracket> resolved = subject.resolve(new BigDecimal("30000.00"));

        // Assert: the middle bracket is returned.
        assertThat(resolved).contains(BRACKET_2);
    }

    @Test
    @DisplayName("resolveFor_invalidInput_throwsInvalidIncomeException")
    void resolveFor_invalidInput_throwsInvalidIncomeException() {
        // Arrange: a progressive resolver with three ascending CALIFORNIA brackets.
        ProgressiveStateBracketResolver subject = new ProgressiveStateBracketResolver(
                JURISDICTION, List.of(BRACKET_1, BRACKET_2, BRACKET_3));

        // Act + Assert: a negative taxable amount is rejected as invalid input.
        assertThatThrownBy(() -> subject.resolve(new BigDecimal("-1.00")))
                .isInstanceOf(InvalidIncomeException.class)
                .hasMessageContaining("-1.00");
    }

    @Test
    @DisplayName("resolveFor_amountAtBracketFloor_returnsThatBracket")
    void resolveFor_amountAtBracketFloor_returnsThatBracket() {
        // Arrange: a progressive resolver with three ascending CALIFORNIA brackets.
        ProgressiveStateBracketResolver subject = new ProgressiveStateBracketResolver(
                JURISDICTION, List.of(BRACKET_1, BRACKET_2, BRACKET_3));

        // Act: resolve the exact floor of the top, unbounded bracket.
        Optional<TaxBracket> resolved = subject.resolve(new BigDecimal("50000.00"));

        // Assert: the floor is inclusive, so the top bracket is returned, not the middle one.
        assertThat(resolved).contains(BRACKET_3);
    }

    @Test
    @DisplayName("resolveFor_amountRequiringExtendedTable_throwsBracketResolutionFailedException")
    void resolveFor_amountRequiringExtendedTable_throwsBracketResolutionFailedException() {
        // Arrange: a progressive resolver with three ascending CALIFORNIA brackets.
        ProgressiveStateBracketResolver subject = new ProgressiveStateBracketResolver(
                JURISDICTION, List.of(BRACKET_1, BRACKET_2, BRACKET_3));

        // Act + Assert: an amount beyond the extended-table threshold surfaces the
        // synthetic upstream load failure as a domain exception.
        assertThatThrownBy(() -> subject.resolve(new BigDecimal("1000000000")))
                .isInstanceOf(BracketResolutionFailedException.class)
                .hasMessageContaining(JURISDICTION);
    }

    @Test
    @DisplayName("constructorFor_blankJurisdiction_throwsIllegalArgumentException")
    void constructorFor_blankJurisdiction_throwsIllegalArgumentException() {
        // Arrange: no subject yet — the constructor call itself is under test.

        // Act + Assert: a blank jurisdiction is rejected as nonsensical input.
        assertThatThrownBy(() -> new ProgressiveStateBracketResolver("   ", List.of(BRACKET_1)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("constructorFor_emptyBrackets_throwsIllegalArgumentException")
    void constructorFor_emptyBrackets_throwsIllegalArgumentException() {
        // Arrange: no subject yet — the constructor call itself is under test.

        // Act + Assert: an empty bracket list leaves nothing to resolve against.
        assertThatThrownBy(() -> new ProgressiveStateBracketResolver(JURISDICTION, List.of()))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("constructorFor_bracketFromAnotherJurisdiction_throwsIllegalArgumentException")
    void constructorFor_bracketFromAnotherJurisdiction_throwsIllegalArgumentException() {
        // Arrange: a bracket that belongs to a different jurisdiction than the resolver.
        TaxBracket foreignBracket = new TaxBracket(
                "ny-bracket-1", "NEW YORK", new BigDecimal("0.04"), new BigDecimal("0"), new BigDecimal("10000"));

        // Act + Assert: mixing jurisdictions is rejected as nonsensical input.
        assertThatThrownBy(() -> new ProgressiveStateBracketResolver(JURISDICTION, List.of(foreignBracket)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("resolveFor_amountInAGapBetweenBrackets_returnsEmpty")
    void resolveFor_amountInAGapBetweenBrackets_returnsEmpty() {
        // Arrange: a resolver missing the middle bracket, leaving a 20000-50000 gap.
        ProgressiveStateBracketResolver subject = new ProgressiveStateBracketResolver(
                JURISDICTION, List.of(BRACKET_1, BRACKET_3));

        // Act: resolve an amount that falls inside the gap.
        Optional<TaxBracket> resolved = subject.resolve(new BigDecimal("30000.00"));

        // Assert: no bracket covers the amount, so resolution comes back empty.
        assertThat(resolved).isEmpty();
    }

    @Test
    @DisplayName("equalsAndHashCodeFor_resolversWithSameJurisdictionAndBrackets_areEqual")
    void equalsAndHashCodeFor_resolversWithSameJurisdictionAndBrackets_areEqual() {
        // Arrange: two resolvers built from identical arguments, plus one that differs.
        ProgressiveStateBracketResolver subject = new ProgressiveStateBracketResolver(
                JURISDICTION, List.of(BRACKET_1, BRACKET_2, BRACKET_3));
        ProgressiveStateBracketResolver sameArguments = new ProgressiveStateBracketResolver(
                JURISDICTION, List.of(BRACKET_1, BRACKET_2, BRACKET_3));
        ProgressiveStateBracketResolver differentBrackets = new ProgressiveStateBracketResolver(
                JURISDICTION, List.of(BRACKET_1));

        // Act + Assert: reflexivity, value equality, and inequality against another type/value.
        assertThat(subject).isEqualTo(subject);
        assertThat(subject).isEqualTo(sameArguments).hasSameHashCodeAs(sameArguments);
        assertThat(subject).isNotEqualTo(differentBrackets);
        assertThat(subject).isNotEqualTo("not a resolver");
    }
}
