package com.uptimecrew.tax_liability.config;

import com.uptimecrew.tax_liability.llm.cost.CostLogger;
import com.uptimecrew.tax_liability.llm.cost.CostMiddleware;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Wires the LLM cost-tracking path (W6 D4 Task 2).
 *
 * <p>Two beans, both plain objects with no Spring annotations of their own. That is deliberate:
 * {@link CostMiddleware} and {@link CostLogger} carry the cost arithmetic and the EMF line
 * format, and both are worth unit-testing by construction rather than through an application
 * context. Keeping the framework wiring here instead of on the classes means
 * {@code CostMiddlewareTest} builds them with {@code new} in a few microseconds, while the
 * application still gets them injected normally.
 */
@Configuration
public class CostTrackingConfig {

    /**
     * The structured cost log.
     *
     * <p>Built with its own {@link com.fasterxml.jackson.databind.ObjectMapper} rather than the
     * application's shared one. The EMF line's shape is a contract with CloudWatch, and the
     * shared mapper carries application-wide serialisation configuration that a future,
     * unrelated change could alter - a global {@code NON_NULL} inclusion or a naming strategy
     * would silently drop or rename a dimension value, and CloudWatch answers a malformed
     * directive by discarding the metric and keeping the log line. That failure reads as "the
     * cost series went quiet", which is indistinguishable from "nothing spent money".
     */
    @Bean
    public CostLogger costLogger() {
        return new CostLogger();
    }

    /**
     * The middleware every LLM call is routed through.
     *
     * @param costLogger the cost logger to emit through
     */
    @Bean
    public CostMiddleware costMiddleware(CostLogger costLogger) {
        return new CostMiddleware(costLogger);
    }
}
