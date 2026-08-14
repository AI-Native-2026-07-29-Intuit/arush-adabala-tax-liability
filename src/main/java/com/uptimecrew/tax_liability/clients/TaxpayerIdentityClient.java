package com.uptimecrew.tax_liability.clients;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

/**
 * Declarative HTTP client: Spring generates the proxy from this interface plus the {@code
 * identity.base-url} property. The Feign proxy does NOT run through the Resilience4j advisor —
 * that's why the call is wrapped in {@link IdentityService} rather than annotated here.
 */
@FeignClient(name = "identity", url = "${identity.base-url}")
public interface TaxpayerIdentityClient {

    @GetMapping("/identity/{userId}/profile")
    IdentityProfile getProfile(@PathVariable("userId") String userId);
}
