package com.uptimecrew.tax_liability.api;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.util.Map;
import java.util.Objects;
import java.util.function.Supplier;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;

/**
 * POST-once semantics for write-shaped endpoints, backed by the W2 D5 Redis instance. Behaviour:
 * (1) compute the Redis key {@code idem:{namespace}:{key}}; (2) if a value already exists,
 * deserialise and return the cached response; (3) else {@code SETNX} a sentinel with a 24h TTL,
 * returning 409 Conflict on a race; (4) otherwise run {@code doWork}, serialise + store the
 * result under the same key with the same TTL, and return it.
 */
@Service
public class IdempotencyService {

    private static final Logger LOG = LoggerFactory.getLogger(IdempotencyService.class);
    private static final Duration TTL = Duration.ofHours(24);
    private static final String IN_FLIGHT = "__in_flight__";

    private final StringRedisTemplate redis;
    private final ObjectMapper mapper;

    public IdempotencyService(StringRedisTemplate redis, ObjectMapper mapper) {
        this.redis = Objects.requireNonNull(redis, "redis must not be null");
        this.mapper = Objects.requireNonNull(mapper, "mapper must not be null");
    }

    @SuppressWarnings("unchecked")
    public <T> ResponseEntity<T> handle(String key, String namespace, Supplier<ResponseEntity<T>> doWork) {
        String redisKey = "idem:" + namespace + ":" + key;
        String existing = redis.opsForValue().get(redisKey);

        if (existing != null && !IN_FLIGHT.equals(existing)) {
            LOG.info("idempotency cache hit key={} namespace={}", key, namespace);
            try {
                Map<String, Object> body = mapper.readValue(existing, new TypeReference<>() {
                });
                return (ResponseEntity<T>) ResponseEntity.ok(body);
            } catch (Exception ex) {
                LOG.warn("idempotency cached body unreadable; recomputing key={}", key);
            }
        }

        Boolean acquired = redis.opsForValue().setIfAbsent(redisKey, IN_FLIGHT, TTL);
        if (Boolean.FALSE.equals(acquired)) {
            LOG.warn("idempotency conflict key={} namespace={}", key, namespace);
            return ResponseEntity.status(HttpStatus.CONFLICT).build();
        }

        ResponseEntity<T> result = doWork.get();
        try {
            String serialised = mapper.writeValueAsString(result.getBody());
            redis.opsForValue().set(redisKey, serialised, TTL);
        } catch (Exception ex) {
            LOG.warn("failed to cache idempotent body key={}; cache miss on retry", key, ex);
            redis.delete(redisKey);
        }
        return result;
    }
}
