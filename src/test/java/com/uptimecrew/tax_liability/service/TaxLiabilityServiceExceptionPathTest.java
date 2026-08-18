package com.uptimecrew.tax_liability.service;

import java.io.IOException;
import java.math.BigDecimal;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.exception.BracketResolutionFailedException;
import com.uptimecrew.tax_liability.exception.InvalidIncomeException;
import com.uptimecrew.tax_liability.outbox.EventOutboxRepository;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;
import com.uptimecrew.tax_liability.repository.TaxpayerRepository;

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
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TaxLiabilityServiceExceptionPathTest {

    @Mock
    BracketResolver strategy;

    @Mock
    TaxpayerRepository repository;

    @Mock
    TaxpayerReadModelRepository readModelRepository;

    @Mock
    EventOutboxRepository outboxRepository;

    ObjectMapper objectMapper = new ObjectMapper().findAndRegisterModules();

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
    void throws_typed_domain_exception_on_invalid_input() {
        when(strategy.resolve(any()))
                .thenThrow(new InvalidIncomeException("income must be non-null and non-negative"));

        TaxLiabilityService subject =
                new TaxLiabilityService(strategy, repository, readModelRepository, outboxRepository, objectMapper);

        assertThatThrownBy(() -> subject.computeLiability("taxpayer-001", "Ada Lovelace", "SINGLE", new BigDecimal("-100.00")))
                .isInstanceOf(InvalidIncomeException.class)
                .hasMessageContaining("income must be non-null and non-negative");
    }

    @Test
    void preserves_underlying_cause_on_bracket_resolution_failure() {
        IOException cause = new IOException("synthetic cause");
        when(strategy.resolve(any()))
                .thenThrow(new BracketResolutionFailedException("failed loading bracket table for jurisdiction CA", cause));

        TaxLiabilityService subject =
                new TaxLiabilityService(strategy, repository, readModelRepository, outboxRepository, objectMapper);

        assertThatThrownBy(() -> subject.computeLiability("taxpayer-001", "Ada Lovelace", "SINGLE", new BigDecimal("1000.00")))
                .isInstanceOf(BracketResolutionFailedException.class)
                .hasRootCauseInstanceOf(IOException.class);
    }

    @Test
    void emits_a_single_warn_log_line_containing_the_exception_message() {
        when(strategy.resolve(any()))
                .thenThrow(new InvalidIncomeException("income must be non-null and non-negative"));

        TaxLiabilityService subject =
                new TaxLiabilityService(strategy, repository, readModelRepository, outboxRepository, objectMapper);

        assertThatThrownBy(() -> subject.computeLiability("taxpayer-001", "Ada Lovelace", "SINGLE", new BigDecimal("-100.00")))
                .isInstanceOf(InvalidIncomeException.class);

        assertThat(appender.list)
                .filteredOn(event -> event.getLevel() == Level.WARN)
                .hasSize(1)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anyMatch(message -> message.contains("income must be non-null and non-negative"));
    }
}
