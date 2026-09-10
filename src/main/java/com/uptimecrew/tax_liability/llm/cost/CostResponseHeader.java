package com.uptimecrew.tax_liability.llm.cost;

import java.math.BigDecimal;

import jakarta.servlet.http.HttpServletResponse;

/**
 * Surfaces the per-request LLM cost as the {@code X-Cost-Usd} response header (W6 D4 Task 2).
 *
 * <p>This is the value the W6 D5 k6 cost threshold reads, which is what turns that threshold from
 * a load-test decoration into a real, enforceable gate: the number in the header is the same
 * number the structured cost log records, computed once by {@link CostMiddleware} from the
 * provider's own token counts.
 *
 * <p><b>The header is formatted with {@link BigDecimal}, not {@code Double.toString}.</b> The
 * reference implementation for this task divides in {@code double} and stringifies the result,
 * and that is wrong for the value range this header actually carries. {@code Double.toString}
 * switches to scientific notation below {@code 1e-3} - a typical Haiku explain-liability call
 * costs on the order of $0.0002, so the header would read {@code 2.0E-4}. That is a number a
 * chart axis, a k6 threshold expression or a shell {@code awk} pipeline will mis-parse or reject,
 * and the mis-parse silently reads as a very small or zero cost rather than as an error.
 * {@link BigDecimal#toPlainString()} on a scaled {@code long} is exact, never uses an exponent,
 * and cannot round: {@code CostResponseHeaderTest} asserts both spellings side by side so the
 * difference is a test failure if anyone reverts it.
 */
public final class CostResponseHeader {

    /** The header name. One constant, because a test and a k6 threshold both reference it. */
    public static final String HEADER = "X-Cost-Usd";

    /**
     * Scale of the integer minor unit: cost is carried as an integer count of 1e-5 USD.
     * See {@link CostMiddleware} for why this scale and not the usual money scale of 2.
     */
    static final int COST_SCALE = 5;

    private CostResponseHeader() {
        throw new AssertionError("CostResponseHeader is not instantiable");
    }

    /**
     * Attach {@code X-Cost-Usd} to the response, if there is one.
     *
     * @param response   the servlet response, or null for a call made outside an HTTP request
     *                   (a scheduled job, a warm-up). Null is a no-op rather than an error: such
     *                   a call is still costed and still logged, it just has nowhere to put a
     *                   header, and making that a failure would mean the cost path could only be
     *                   used from a controller.
     * @param costUsdE5  cost in integer units of 1e-5 USD; must not be negative
     * @throws IllegalArgumentException if {@code costUsdE5} is negative
     */
    public static void attach(HttpServletResponse response, long costUsdE5) {
        if (costUsdE5 < 0) {
            throw new IllegalArgumentException("costUsdE5 must not be negative, was " + costUsdE5);
        }
        if (response == null) {
            return;
        }
        response.setHeader(HEADER, format(costUsdE5));
    }

    /**
     * Render integer minor units as a plain decimal USD string, e.g. {@code 20} to
     * {@code "0.00020"}.
     *
     * <p>Package-private and separately tested, so the formatting claim above is verified
     * independently of a servlet.
     *
     * @param costUsdE5 cost in integer units of 1e-5 USD
     * @return an exact, exponent-free decimal string with {@value #COST_SCALE} fraction digits
     */
    static String format(long costUsdE5) {
        return BigDecimal.valueOf(costUsdE5, COST_SCALE).toPlainString();
    }
}
