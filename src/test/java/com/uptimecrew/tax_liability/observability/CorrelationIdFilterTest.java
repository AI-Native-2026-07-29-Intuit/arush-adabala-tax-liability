package com.uptimecrew.tax_liability.observability;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.UUID;

import jakarta.servlet.FilterChain;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/**
 * Unit coverage for {@link CorrelationIdFilter}: what ends up in MDC and on the response for each
 * shape of inbound header, and - the part that matters most in a thread-pooled server - that MDC
 * is left clean afterwards either way.
 */
class CorrelationIdFilterTest {

    private CorrelationIdFilter filter;
    private MockHttpServletRequest request;
    private MockHttpServletResponse response;

    @BeforeEach
    void setUp() {
        filter = new CorrelationIdFilter();
        request = new MockHttpServletRequest("GET", "/api/v1/taxpayers/txp_synth_001");
        response = new MockHttpServletResponse();
    }

    @AfterEach
    void clearMdc() {
        MDC.clear();
    }

    @Test
    void usesTheCallersCorrelationIdAndEchoesItBack() throws Exception {
        request.addHeader(CorrelationIdFilter.HEADER, "ticket-4711");

        String seenInsideChain = runFilter();

        assertThat(seenInsideChain).isEqualTo("ticket-4711");
        assertThat(response.getHeader(CorrelationIdFilter.HEADER)).isEqualTo("ticket-4711");
    }

    @Test
    void generatesAUuidWhenTheCallerSendsNoHeader() throws Exception {
        String seenInsideChain = runFilter();

        assertThat(seenInsideChain).isNotNull();
        assertThatCode(() -> UUID.fromString(seenInsideChain));
        assertThat(response.getHeader(CorrelationIdFilter.HEADER)).isEqualTo(seenInsideChain);
    }

    @Test
    void generatesAUuidWhenTheHeaderIsBlank() throws Exception {
        request.addHeader(CorrelationIdFilter.HEADER, "   ");

        String seenInsideChain = runFilter();

        assertThatCode(() -> UUID.fromString(seenInsideChain));
    }

    @Test
    void rejectsAHeaderCarryingControlCharacters() throws Exception {
        // A CR/LF in a value that is written straight back into a response header is a header
        // injection primitive; the value is replaced outright rather than sanitised.
        request.addHeader(CorrelationIdFilter.HEADER, "abc\r\nX-Injected: yes");

        String seenInsideChain = runFilter();

        assertThat(seenInsideChain).doesNotContain("X-Injected");
        assertThatCode(() -> UUID.fromString(seenInsideChain));
    }

    @Test
    void rejectsAnOverlongHeader() throws Exception {
        request.addHeader(CorrelationIdFilter.HEADER, "x".repeat(129));

        String seenInsideChain = runFilter();

        assertThatCode(() -> UUID.fromString(seenInsideChain));
    }

    @Test
    void removesTheMdcEntryAfterTheRequest() throws Exception {
        request.addHeader(CorrelationIdFilter.HEADER, "ticket-4711");

        runFilter();

        // Tomcat reuses request threads: a leftover entry would relabel the next, unrelated
        // request's log lines with this request's id.
        assertThat(MDC.get(CorrelationIdFilter.MDC_KEY)).isNull();
    }

    @Test
    void removesTheMdcEntryEvenWhenTheChainThrows() {
        FilterChain exploding = (req, res) -> {
            throw new IllegalStateException("downstream blew up");
        };

        assertThatThrownBy(() -> filter.doFilter(request, response, exploding))
                .isInstanceOf(IllegalStateException.class);

        assertThat(MDC.get(CorrelationIdFilter.MDC_KEY)).isNull();
    }

    /**
     * Runs the filter with a chain that captures whatever MDC held while the request was being
     * handled - the only moment the value is observable, since the filter clears it on the way out.
     *
     * @return the correlation id visible to the rest of the chain
     * @throws Exception if the filter or the chain fails
     */
    private String runFilter() throws Exception {
        String[] captured = new String[1];
        FilterChain chain = (req, res) -> captured[0] = MDC.get(CorrelationIdFilter.MDC_KEY);
        filter.doFilter(request, response, chain);
        return captured[0];
    }

    /**
     * @param runnable a call expected not to throw; used to assert a value parses as a UUID
     */
    private static void assertThatCode(Runnable runnable) {
        org.assertj.core.api.Assertions.assertThatCode(runnable::run).doesNotThrowAnyException();
    }
}
