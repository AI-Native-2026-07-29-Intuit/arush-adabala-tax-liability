package com.uptimecrew.tax_liability.orders;

import java.util.Objects;

/**
 * Thrown when an order does not exist for the requesting tenant.
 *
 * <p>Deliberately does not distinguish "no such order" from "that order belongs to someone
 * else". Telling the two apart confirms the existence of another tenant's order to anyone who
 * can guess an id, which turns a 404 into an enumeration oracle.
 */
public final class OrderNotFoundException extends RuntimeException {

    private static final long serialVersionUID = 1L;

    private final String orderId;

    /**
     * Creates the exception.
     *
     * @param orderId the order that could not be read
     * @throws NullPointerException if {@code orderId} is {@code null}
     */
    public OrderNotFoundException(String orderId) {
        super("order not found: " + Objects.requireNonNull(orderId, "orderId must not be null"));
        this.orderId = orderId;
    }

    /**
     * Returns the order id that could not be read.
     *
     * @return the order id
     */
    public String orderId() {
        return orderId;
    }
}
