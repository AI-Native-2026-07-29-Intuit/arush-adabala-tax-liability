package com.uptimecrew.tax_liability;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cache.annotation.EnableCaching;
import org.springframework.cloud.openfeign.EnableFeignClients;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * The Spring Boot entry point for the taxcalc service: REST + GraphQL APIs, the Kafka
 * transactional-outbox event pipeline, and the Model Context Protocol server all boot from this
 * one application context.
 */
@SpringBootApplication
@EnableCaching
@EnableFeignClients
@EnableScheduling
public class Application {

    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
