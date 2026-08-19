package com.uptimecrew.tax_liability.api;

import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

import com.uptimecrew.tax_liability.clients.IdentityProfile;
import com.uptimecrew.tax_liability.clients.IdentityService;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.service.TaxLiabilityService;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;
import io.swagger.v3.oas.annotations.tags.Tag;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * The controller moves under {@code /api/v1/taxpayers} (URI versioning). The summary route is
 * now a POST, requires an {@code Idempotency-Key} header, and enriches the response with the
 * caller's display name resolved via the Feign-backed {@link IdentityService}. Gated by the
 * {@link com.uptimecrew.tax_liability.security.SecurityConfig} filter chain: every route requires
 * a Bearer JWT carrying both the {@code taxpayers.read} scope and the {@code TAXPAYER_READER}
 * role, EXCEPT {@link #create} (W3 D5), which requires the narrower {@code taxpayers.write}
 * scope and {@code TAXPAYER_WRITER} role instead - a read-only caller should not also be able to
 * write. {@link #summary} additionally sits behind
 * {@link com.uptimecrew.tax_liability.security.RateLimitFilter}, since it stubs an LLM call that
 * will cost real money once Week 5 wires up a real model.
 */
@RestController
@RequestMapping("/api/v1/taxpayers")
@Tag(name = "Taxpayers", description = "Taxpayers read/write API and LLM-summary POST endpoint")
public class TaxpayerController {

    private static final Logger LOG = LoggerFactory.getLogger(TaxpayerController.class);
    private static final String READ_AUTHORITY =
            "hasAuthority('SCOPE_taxpayers.read') and hasRole('TAXPAYER_READER')";
    private static final String WRITE_AUTHORITY =
            "hasAuthority('SCOPE_taxpayers.write') and hasRole('TAXPAYER_WRITER')";

    private final TaxLiabilityService service;
    private final IdentityService identityService;
    private final IdempotencyService idempotency;

    public TaxpayerController(TaxLiabilityService service, IdentityService identityService,
            IdempotencyService idempotency) {
        this.service = Objects.requireNonNull(service, "service must not be null");
        this.identityService = Objects.requireNonNull(identityService, "identityService must not be null");
        this.idempotency = Objects.requireNonNull(idempotency, "idempotency must not be null");
    }

    @GetMapping("/{id}")
    @PreAuthorize(READ_AUTHORITY)
    @Operation(summary = "Fetch a taxpayer by id",
            description = "Returns the denormalised read-model document for the given id.")
    @ApiResponses({
        @ApiResponse(responseCode = "200", description = "Found"),
        @ApiResponse(responseCode = "401", description = "Missing or invalid JWT"),
        @ApiResponse(responseCode = "403", description = "JWT present but lacks required scope or role"),
        @ApiResponse(responseCode = "404", description = "No taxpayer with that id")
    })
    public ResponseEntity<TaxpayerReadModel> getById(@PathVariable String id, @AuthenticationPrincipal Jwt jwt) {
        LOG.info("get id={} subject={}", id, jwt.getSubject());
        Optional<TaxpayerReadModel> found = service.findById(id);
        return found.map(ResponseEntity::ok).orElseGet(() -> ResponseEntity.notFound().build());
    }

    @PostMapping
    @PreAuthorize(WRITE_AUTHORITY)
    @Operation(summary = "Record a taxpayer's tax liability",
            description = "Resolves the taxpayer's bracket, persists the domain write, and publishes the "
                    + "resulting update to Kafka via the transactional outbox.")
    @ApiResponses({
        @ApiResponse(responseCode = "201", description = "Created"),
        @ApiResponse(responseCode = "401", description = "Missing or invalid JWT"),
        @ApiResponse(responseCode = "403", description = "JWT present but lacks required scope or role")
    })
    public ResponseEntity<TaxpayerReadModel> create(@RequestBody CreateTaxpayerRequest request,
            @AuthenticationPrincipal Jwt jwt) {
        LOG.info("create id={} subject={}", request.id(), jwt.getSubject());
        service.computeLiability(request.id(), request.displayName(), request.filingStatus(),
                request.taxableAmount());
        TaxpayerReadModel created = service.findById(request.id())
                .orElseThrow(() -> new IllegalStateException("just-created taxpayer not found: " + request.id()));
        return ResponseEntity.status(HttpStatus.CREATED).body(created);
    }

    @PostMapping("/{id}/summary")
    @PreAuthorize(READ_AUTHORITY)
    @Operation(summary = "Generate an LLM summary for a taxpayer",
            description = "Idempotent POST: pass an Idempotency-Key header (UUID) so retries return the same result.")
    @ApiResponses({
        @ApiResponse(responseCode = "200", description = "Summary generated (or cached body returned)"),
        @ApiResponse(responseCode = "400", description = "Idempotency-Key missing or not a valid UUID"),
        @ApiResponse(responseCode = "401", description = "Missing or invalid JWT"),
        @ApiResponse(responseCode = "403", description = "JWT present but lacks required scope or role"),
        @ApiResponse(responseCode = "409", description = "Idempotency key in flight for a different request")
    })
    public ResponseEntity<Map<String, Object>> summary(@PathVariable String id,
            @RequestHeader("Idempotency-Key") String idempotencyKey, @AuthenticationPrincipal Jwt jwt) {
        LOG.info("summary id={} subject={}", id, jwt.getSubject());

        final UUID parsed;
        try {
            parsed = UUID.fromString(idempotencyKey);
        } catch (IllegalArgumentException ex) {
            LOG.warn("rejected non-UUID Idempotency-Key for id={} subject={}", id, jwt.getSubject());
            return ResponseEntity.badRequest().build();
        }

        return idempotency.handle(parsed.toString(), "taxpayers.summary", () -> {
            try {
                Thread.sleep(100); // (1) stub LLM latency; Week 5 replaces this with a real call
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
            }
            IdentityProfile profile = identityService.getProfile(jwt.getSubject());
            LOG.info("summary id={} subject={} displayName={}", id, jwt.getSubject(), profile.displayName());
            return ResponseEntity.ok(Map.of(
                    "summary", "Stub LLM summary for " + id,
                    "displayName", profile.displayName()));
        });
    }
}
