package com.uptimecrew.tax_liability.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.catchThrowable;

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

        // Act: resolve a negative taxable amount.
        Throwable thrown = catchThrowable(() -> subject.resolve(new BigDecimal("-1.00")));

        // Assert: rejected as invalid input.
        assertThat(thrown).isInstanceOf(InvalidIncomeException.class).hasMessageContaining("-1.00");
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

        // Act: resolve an amount beyond the extended-table threshold.
        Throwable thrown = catchThrowable(() -> subject.resolve(new BigDecimal("1000000000")));

        // Assert: the synthetic upstream load failure surfaces as a domain exception.
        assertThat(thrown).isInstanceOf(BracketResolutionFailedException.class).hasMessageContaining(JURISDICTION);
    }

    @Test
    @DisplayName("constructorFor_blankJurisdiction_throwsIllegalArgumentException")
    void constructorFor_blankJurisdiction_throwsIllegalArgumentException() {
        // Arrange: no subject yet — the constructor call itself is under test.

        // Act: construct a resolver with a blank jurisdiction.
        Throwable thrown = catchThrowable(() -> new ProgressiveStateBracketResolver("   ", List.of(BRACKET_1)));

        // Assert: rejected as nonsensical input.
        assertThat(thrown).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("constructorFor_emptyBrackets_throwsIllegalArgumentException")
    void constructorFor_emptyBrackets_throwsIllegalArgumentException() {
        // Arrange: no subject yet — the constructor call itself is under test.

        // Act: construct a resolver with an empty bracket list.
        Throwable thrown = catchThrowable(() -> new ProgressiveStateBracketResolver(JURISDICTION, List.of()));

        // Assert: rejected — there is nothing to resolve against.
        assertThat(thrown).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("constructorFor_bracketFromAnotherJurisdiction_throwsIllegalArgumentException")
    void constructorFor_bracketFromAnotherJurisdiction_throwsIllegalArgumentException() {
        // Arrange: a bracket that belongs to a different jurisdiction than the resolver.
        TaxBracket foreignBracket = new TaxBracket(
                "ny-bracket-1", "NEW YORK", new BigDecimal("0.04"), new BigDecimal("0"), new BigDecimal("10000"));

        // Act: construct a resolver whose brackets include one from another jurisdiction.
        Throwable thrown =
                catchThrowable(() -> new ProgressiveStateBracketResolver(JURISDICTION, List.of(foreignBracket)));

        // Assert: mixing jurisdictions is rejected as nonsensical input.
        assertThat(thrown).isInstanceOf(IllegalArgumentException.class);
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

        // Act: compare the subject against itself, an equal value, a differing value, and another type.
        boolean equalToItself = subject.equals(subject);
        boolean equalToSameArguments = subject.equals(sameArguments);
        boolean equalToDifferentBrackets = subject.equals(differentBrackets);
        boolean equalToNonResolver = subject.equals("not a resolver");

        // Assert: reflexivity, value equality (with matching hashCode), and both inequalities hold.
        assertThat(equalToItself).isTrue();
        assertThat(equalToSameArguments).isTrue();
        assertThat(subject).hasSameHashCodeAs(sameArguments);
        assertThat(equalToDifferentBrackets).isFalse();
        assertThat(equalToNonResolver).isFalse();
    }
}
