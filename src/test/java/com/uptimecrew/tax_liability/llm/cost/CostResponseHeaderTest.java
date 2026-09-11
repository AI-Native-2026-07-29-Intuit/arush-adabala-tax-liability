package com.uptimecrew.tax_liability.llm.cost;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.mock.web.MockHttpServletResponse;

/**
 * The {@code X-Cost-Usd} header format (W6 D4 Task 2).
 *
 * <p>This suite exists mainly for {@link #doubleToStringWouldEmitScientificNotation()}, which
 * pins a claim the production javadoc makes rather than leaving it as an assertion in a comment.
 */
class CostResponseHeaderTest {

    @ParameterizedTest(name = "{0}e-5 USD renders as {1}")
    @CsvSource({
        "450,   0.00450",
        "20,    0.00020",
        "0,     0.00000",
        "1,     0.00001",
        "100000, 1.00000",
    })
    void rendersPlainDecimalUsd(long costUsdE5, String expected) {
        assertThat(CostResponseHeader.format(costUsdE5)).isEqualTo(expected);
    }

    /**
     * The reason this class does not use the reference implementation's
     * {@code Double.toString(costUsdE5 / 100_000.0)}.
     *
     * <p>Java switches {@code Double.toString} to scientific notation below 1e-3. A typical Haiku
     * explain-liability call costs on the order of $0.0002, so the header would carry
     * {@code 2.0E-4} - a string a k6 threshold expression, a chart axis or a shell {@code awk}
     * pipeline will mis-parse or reject, and the mis-parse reads as a near-zero cost rather than
     * as an error. This test asserts BOTH spellings so the difference is a visible failure if
     * anyone reverts the formatting, rather than a claim in a comment nobody re-checks.
     */
    @Test
    void doubleToStringWouldEmitScientificNotation() {
        long costUsdE5 = 20L; // $0.00020, a realistic single-call Haiku cost

        String viaDouble = Double.toString(costUsdE5 / 100_000.0);
        String viaBigDecimal = CostResponseHeader.format(costUsdE5);

        assertThat(viaDouble)
                .as("the reference implementation's formatting, kept here as the counter-example")
                .isEqualTo("2.0E-4")
                .containsIgnoringCase("e");
        assertThat(viaBigDecimal)
                .as("plain, exponent-free, exactly parseable")
                .isEqualTo("0.00020")
                .doesNotContainIgnoringCase("e");

        // Both denote the same quantity - the bug is purely in how it is written down, which is
        // exactly why it survives review.
        assertThat(Double.parseDouble(viaDouble)).isEqualTo(Double.parseDouble(viaBigDecimal));
    }

    @Test
    void attachesHeaderToResponse() {
        MockHttpServletResponse response = new MockHttpServletResponse();

        CostResponseHeader.attach(response, 450L);

        assertThat(response.getHeader("X-Cost-Usd")).isEqualTo("0.00450");
    }

    /** A detached call has nowhere to put a header; that is a no-op, not a failure. */
    @Test
    void nullResponseIsANoOp() {
        CostResponseHeader.attach(null, 450L);
    }

    @Test
    void rejectsNegativeCost() {
        MockHttpServletResponse response = new MockHttpServletResponse();
        assertThrows(IllegalArgumentException.class, () -> CostResponseHeader.attach(response, -1L));
    }
}
