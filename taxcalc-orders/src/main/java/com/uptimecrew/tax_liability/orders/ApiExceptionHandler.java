package com.uptimecrew.tax_liability.orders;

import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * Turns this service's failures into the status codes the MCP layer maps to error codes.
 *
 * <p><strong>Why this class is not optional.</strong> The MCP server translates HTTP statuses
 * into numeric codes its callers branch on - 404 becomes 4040, 409 becomes 4090, and anything
 * unmapped becomes a generic 5030 meaning "the server broke". Without these handlers, a caller
 * error like a malformed amount would surface as a 500 and therefore as 5030, telling the LLM
 * client that retrying is pointless when in fact the fix is to correct its own argument. The
 * status code is the contract; getting it wrong here silently corrupts the semantics two layers
 * up.
 */
@RestControllerAdvice
public final class ApiExceptionHandler {

    /**
     * Maps a missing order to 404.
     *
     * @param exception the failure
     * @return a 404 naming the order
     */
    @ExceptionHandler(OrderNotFoundException.class)
    public ResponseEntity<Map<String, String>> notFound(OrderNotFoundException exception) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Map.of("error", "order not found", "order_id", exception.orderId()));
    }

    /**
     * Maps a nonsensical argument to 400.
     *
     * <p>Covers both the compact-constructor validation in {@link CreateRefundRequest} and the
     * amount checks in {@link Money}, so a caller sending a negative amount is told it is a bad
     * request rather than being handed a 500.
     *
     * @param exception the failure
     * @return a 400 carrying the validation message
     */
    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<Map<String, String>> badRequest(IllegalArgumentException exception) {
        return ResponseEntity.badRequest().body(Map.of("error", exception.getMessage()));
    }

    /**
     * Maps an unparseable body to 400.
     *
     * <p>Jackson wraps the compact constructor's {@link IllegalArgumentException} in an
     * {@link HttpMessageNotReadableException} when validation fails during deserialisation, so
     * without this handler the careful 400 above would still be reported as a 500. The cause is
     * unwrapped so the caller is told which field was wrong rather than being shown Jackson's
     * internal path.
     *
     * @param exception the failure
     * @return a 400 carrying the underlying validation message
     */
    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<Map<String, String>> unreadable(
            HttpMessageNotReadableException exception) {
        Throwable cause = exception.getMostSpecificCause();
        String message =
                cause instanceof IllegalArgumentException || cause instanceof NullPointerException
                        ? cause.getMessage()
                        : "request body could not be parsed";
        return ResponseEntity.badRequest()
                .body(Map.of("error", message == null ? "invalid request body" : message));
    }
}
