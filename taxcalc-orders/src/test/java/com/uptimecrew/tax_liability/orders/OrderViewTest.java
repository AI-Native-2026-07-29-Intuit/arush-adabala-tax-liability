package com.uptimecrew.tax_liability.orders;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * Pins the wire shape the Python consumer validates against.
 *
 * <p>The consuming Pydantic models declare {@code extra="forbid"}, so a field renamed on this
 * side does not degrade gracefully over there - it fails validation and the tool call errors.
 * Serialising for real, rather than asserting on the record's accessors, is what makes these
 * tests notice a changed {@code @JsonProperty}.
 */
class OrderViewTest {

    private ObjectMapper mapper;

    @BeforeEach
    void setUp() {
        mapper = new ObjectMapper();
    }

    @Test
    @DisplayName("an order serialises with snake_case names and a string total")
    void serialisesTheAgreedShape() throws Exception {
        String json =
                mapper.writeValueAsString(
                        OrderView.of("ord-synth-9001", "tenant-a", new BigDecimal("42.5"), "paid"));

        assertTrue(json.contains("\"order_id\":\"ord-synth-9001\""), json);
        assertTrue(json.contains("\"tenant_id\":\"tenant-a\""), json);
        assertTrue(json.contains("\"status\":\"paid\""), json);
        // Quoted, and carrying the scale: a JSON number would hand the exactness to whichever
        // float parser read it first, and 42.5 is a different money value from 42.50.
        assertTrue(json.contains("\"total\":\"42.50\""), json);
    }

    @Test
    @DisplayName("a refund serialises with snake_case names and a string amount")
    void refundSerialisesTheAgreedShape() throws Exception {
        String json =
                mapper.writeValueAsString(
                        RefundView.of(
                                "ord-synth-9001",
                                "rfnd-1",
                                new BigDecimal("10"),
                                "duplicate",
                                "refunded"));

        assertTrue(json.contains("\"refund_id\":\"rfnd-1\""), json);
        assertTrue(json.contains("\"amount\":\"10.00\""), json);
    }

    @Test
    @DisplayName("a blank identifier is refused at construction")
    void rejectsBlankIdentifiers() {
        assertThrows(
                IllegalArgumentException.class,
                () -> new OrderView("", "tenant-a", "1.00", "paid"));
        assertThrows(
                NullPointerException.class,
                () -> new OrderView("ord-1", null, "1.00", "paid"));
    }

    @Test
    @DisplayName("records compare by value, so a view can be asserted on directly")
    void comparesByValue() {
        assertEquals(
                OrderView.of("ord-1", "tenant-a", new BigDecimal("1.00"), "paid"),
                OrderView.of("ord-1", "tenant-a", new BigDecimal("1.000"), "paid"));
    }
}
