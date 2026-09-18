package com.uptimecrew.tax_liability.orders;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.math.BigDecimal;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * Pins the request invariants that keep an invalid refund from ever being constructed.
 *
 * <p>The validation lives in the compact constructor rather than in the controller precisely so
 * that these tests can be written without a servlet, and so that no other code path can build one
 * of these objects while skipping the checks.
 */
class CreateRefundRequestTest {

    private static CreateRefundRequest valid() {
        return new CreateRefundRequest(
                "ord-synth-9001", new BigDecimal("10.00"), "duplicate", "key-1");
    }

    @Test
    @DisplayName("a well-formed request keeps the caller's exact amount and scale")
    void acceptsAWellFormedRequest() {
        assertEquals(new BigDecimal("10.00"), valid().amount());
        assertEquals("10.00", valid().amount().toPlainString());
    }

    @Test
    @DisplayName("an absent idempotency key is refused rather than defaulted")
    void rejectsMissingIdempotencyKey() {
        assertThrows(
                NullPointerException.class,
                () ->
                        new CreateRefundRequest(
                                "ord-synth-9001", new BigDecimal("10.00"), "duplicate", null));
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new CreateRefundRequest(
                                "ord-synth-9001", new BigDecimal("10.00"), "duplicate", "  "));
    }

    @Test
    @DisplayName("a non-positive amount is refused")
    void rejectsNonPositiveAmount() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new CreateRefundRequest(
                                "ord-synth-9001", BigDecimal.ZERO, "duplicate", "key-1"));
    }

    @Test
    @DisplayName("a reason outside the recorded length is refused")
    void rejectsOutOfRangeReason() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new CreateRefundRequest(
                                "ord-synth-9001", new BigDecimal("1.00"), "no", "key-1"));
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new CreateRefundRequest(
                                "ord-synth-9001",
                                new BigDecimal("1.00"),
                                "x".repeat(201),
                                "key-1"));
    }
}
