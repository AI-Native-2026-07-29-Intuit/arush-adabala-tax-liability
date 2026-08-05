package com.uptimecrew.tax_liability.service;

import java.math.BigDecimal;
import java.util.Optional;

import com.uptimecrew.tax_liability.model.TaxBracket;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;

import org.slf4j.LoggerFactory;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TaxLiabilityServiceLoggingTest {

    @Mock
    BracketResolver strategy;

    private Logger logbackLogger;
    private ListAppender<ILoggingEvent> appender;

    @BeforeEach
    void attachAppender() {
        logbackLogger = (Logger) LoggerFactory.getLogger(TaxLiabilityService.class);
        appender = new ListAppender<>();
        appender.start();
        logbackLogger.addAppender(appender);
    }

    @AfterEach
    void detachAppender() {
        logbackLogger.detachAppender(appender);
    }

    @Test
    void logs_one_info_line_before_delegating_and_one_on_a_successful_result() {
        TaxBracket resolvedBracket = new TaxBracket(
                "fed-bracket-22", "FEDERAL", new BigDecimal("0.22"), new BigDecimal("47150"), new BigDecimal("100525"));
        when(strategy.resolve(any())).thenReturn(Optional.of(resolvedBracket));

        TaxLiabilityService subject = new TaxLiabilityService(strategy);
        subject.computeLiability(new BigDecimal("75000.00"));

        assertThat(appender.list)
                .filteredOn(event -> event.getLevel() == Level.INFO)
                .hasSize(2)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anyMatch(message -> message.contains("invoking strategy=") && message.contains("75000.00"))
                .anyMatch(message -> message.contains("strategy returned result=16500.00"));
    }
}
