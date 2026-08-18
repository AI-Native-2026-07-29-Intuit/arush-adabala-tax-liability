package com.uptimecrew.tax_liability.outbox;

import java.util.List;

import jakarta.persistence.LockModeType;
import jakarta.persistence.QueryHint;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.QueryHints;

/**
 * Manages {@link EventOutboxEntity} rows in {@code taxcalc.event_outbox}.
 */
public interface EventOutboxRepository extends JpaRepository<EventOutboxEntity, String> {

    // (1) SELECT ... FOR UPDATE SKIP LOCKED, oldest-unpublished-first: PESSIMISTIC_WRITE plus
    // the Hibernate-specific "-2" lock-timeout hint is how Hibernate 6 emits SKIP LOCKED on
    // Postgres. Without SKIP LOCKED, two OutboxPublisher instances polling concurrently would
    // block on each other's row locks instead of splitting the unpublished set between them,
    // and could even double-publish once the blocking transaction commits.
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @QueryHints(@QueryHint(name = "jakarta.persistence.lock.timeout", value = "-2"))
    @Query("select e from EventOutboxEntity e where e.publishedAt is null order by e.occurredAt asc")
    List<EventOutboxEntity> findUnpublishedForUpdate(Pageable pageable);
}
