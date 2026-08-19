package com.uptimecrew.tax_liability.service;

import java.lang.reflect.Constructor;
import java.lang.reflect.InvocationTargetException;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * Covers {@link BracketResolvers}' static factory methods and proves the class is not
 * instantiable (its private constructor throws {@link AssertionError} if reached via reflection).
 */
class BracketResolversTest {

    @Test
    void federal_returns_non_null_resolver() {
        assertNotNull(BracketResolvers.federal());
    }

    @Test
    void federal_returns_equivalent_instances_on_repeated_calls() {
        assertEquals(BracketResolvers.federal(), BracketResolvers.federal());
    }

    @Test
    void flatRateState_returns_equivalent_instances_on_repeated_calls() {
        assertEquals(BracketResolvers.flatRateState(), BracketResolvers.flatRateState());
    }

    @Test
    void noIncomeTaxState_returns_equivalent_instances_on_repeated_calls() {
        assertEquals(BracketResolvers.noIncomeTaxState(), BracketResolvers.noIncomeTaxState());
    }

    @Test
    void constructor_is_not_instantiable_via_reflection() throws NoSuchMethodException {
        Constructor<BracketResolvers> constructor = BracketResolvers.class.getDeclaredConstructor();
        constructor.setAccessible(true);
        InvocationTargetException wrapped = assertThrows(InvocationTargetException.class, constructor::newInstance);
        assertEquals(AssertionError.class, wrapped.getCause().getClass());
    }
}
