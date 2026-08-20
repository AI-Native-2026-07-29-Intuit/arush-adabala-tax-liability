package com.uptimecrew.tax_liability.service;

import java.math.BigDecimal;
import java.util.Optional;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.entity.Taxpayer;
import com.uptimecrew.tax_liability.model.TaxBracket;
import com.uptimecrew.tax_liability.outbox.EventOutboxRepository;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;
import com.uptimecrew.tax_liability.repository.TaxpayerRepository;

import io.opentelemetry.api.OpenTelemetry;

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

import static com.uptimecrew.tax_liability.model.TaxBracketTestDataBuilder.aTaxBracket;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * Covers {@link TaxLiabilityService#computeLiability}'s happy-path logging: exactly one INFO line
 * before delegating to the injected strategy, one on the successful Postgres save, and one on the
 * Mongo write-through - proven with a Logback {@link ListAppender} attached directly to the
 * service's logger, the same pattern {@link TaxLiabilityServiceExceptionPathTest} uses for the
 * failure path.
 */
@ExtendWith(MockitoExtension.class)
class TaxLiabilityServiceLoggingTest {

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
    void logs_one_info_line_before_delegating_and_one_on_a_successful_save() {
        TaxBracket resolvedBracket = aTaxBracket()
                .withId("fed-bracket-22").withJurisdiction("FEDERAL")
                .withRate(new BigDecimal("0.22")).withFloor(new BigDecimal("47150")).withCeiling(new BigDecimal("100525"))
                .build();
        when(strategy.resolve(any())).thenReturn(Optional.of(resolvedBracket));
        when(repository.save(any())).thenAnswer(inv -> inv.getArgument(0));

        TaxLiabilityService subject =
                new TaxLiabilityService(strategy, repository, readModelRepository, outboxRepository, objectMapper,
                OpenTelemetry.noop());
        Taxpayer saved = subject.computeLiability("taxpayer-001", "Ada Lovelace", "SINGLE", new BigDecimal("75000.00"));

        assertThat(appender.list)
                .filteredOn(event -> event.getLevel() == Level.INFO)
                .hasSize(3)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anyMatch(message -> message.contains("invoking strategy=") && message.contains("taxpayer-001"))
                .anyMatch(message -> message.contains("persisted entity id=" + saved.getId()))
                .anyMatch(message -> message.contains("write-through to mongo id=" + saved.getId()));
    }
}
