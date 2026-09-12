package com.uptimecrew.tax_liability.api;

import java.util.List;
import java.util.Map;
import java.util.Objects;

import com.uptimecrew.tax_liability.embeddings.TaxpayerEmbedding;
import com.uptimecrew.tax_liability.embeddings.TaxpayerEmbeddingIngestService;

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
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * The HTTP entry to the embeddings ingest path (W6 D4 Task 3).
 *
 * <p>Deliberately a second controller on {@code /api/v1/taxpayers} rather than two more methods on
 * {@link TaxpayerController}: that class already carries five collaborators, and every
 * {@code @WebMvcTest} slice and constructor call that names it would have to grow a mock for the
 * embeddings stack to add a route that shares nothing with the LLM and idempotency paths. Spring
 * maps both classes under one base path without ambiguity because no (method, path) pair repeats.
 *
 * <p><b>Not behind {@link com.uptimecrew.tax_liability.security.RateLimitFilter}</b>, unlike the
 * two LLM routes on the sibling controller. That filter is a cost control on calls that bill an
 * external party per token; these calls reach a model running on the cluster's own CPU, where the
 * limit that matters is the Deployment's resource envelope and the honest control is capacity, not
 * a 429.
 */
// Non-final, like TaxpayerController: @PreAuthorize is applied by a CGLIB proxy, and Spring cannot
// subclass a final class - a final controller fails context startup rather than a test.
@RestController
@RequestMapping("/api/v1/taxpayers")
@Tag(name = "Taxpayer embeddings",
        description = "pgvector ingest and nearest-neighbour search, backed by the in-cluster embeddings model")
public class TaxpayerEmbeddingController {

    private static final Logger LOG = LoggerFactory.getLogger(TaxpayerEmbeddingController.class);

    private static final String READ_AUTHORITY =
            "hasAuthority('SCOPE_taxpayers.read') and hasRole('TAXPAYER_READER')";
    private static final String WRITE_AUTHORITY =
            "hasAuthority('SCOPE_taxpayers.write') and hasRole('TAXPAYER_WRITER')";

    /** Neighbours returned when the caller names no limit. */
    static final int DEFAULT_LIMIT = 5;

    /** Upper bound on {@code limit}: a caller must not be able to ask for the whole tenant. */
    static final int MAX_LIMIT = 50;

    private final TaxpayerEmbeddingIngestService ingestService;

    /**
     * @param ingestService the only production path from taxpayer text to a stored vector
     * @throws NullPointerException if {@code ingestService} is null
     */
    public TaxpayerEmbeddingController(TaxpayerEmbeddingIngestService ingestService) {
        this.ingestService = Objects.requireNonNull(ingestService, "ingestService must not be null");
    }

    /**
     * Embed this taxpayer's record and store the vector.
     *
     * <p>Idempotent by id derivation rather than by an {@code Idempotency-Key} header: the row id
     * is a function of tenant and taxpayer, so a repeated POST rewrites one row instead of adding
     * another. That is the right shape here - unlike the summary endpoint, a repeat is not a
     * duplicate side effect to suppress, it is a deliberate re-embed after the taxpayer changed.
     *
     * <p>Requires the write scope: this mutates stored state. The response omits the vector - 1024
     * floats are not useful to a caller and would dominate the payload.
     */
    @PostMapping("/{id}/embedding")
    @PreAuthorize(WRITE_AUTHORITY)
    @Operation(summary = "Embed a taxpayer and store the vector",
            description = "POSTs the taxpayer's record to the in-cluster embeddings model "
                    + "(bge-large-en-v1.5, no API key and no third party) and upserts the returned "
                    + "1024-dimension vector into taxcalc.taxpayer_embeddings.")
    @ApiResponses({
        @ApiResponse(responseCode = "201", description = "Embedded and stored"),
        @ApiResponse(responseCode = "401", description = "Missing or invalid JWT"),
        @ApiResponse(responseCode = "403", description = "JWT present but lacks required scope or role"),
        @ApiResponse(responseCode = "404", description = "No taxpayer with that id")
    })
    public ResponseEntity<Map<String, Object>> ingest(@PathVariable String id,
            @AuthenticationPrincipal Jwt jwt) {
        String tenant = tenantOf(jwt);
        LOG.info("embedding ingest id={} subject={} tenant={}", id, jwt.getSubject(), tenant);

        final TaxpayerEmbedding stored;
        try {
            stored = ingestService.ingest(id, tenant);
        } catch (IllegalArgumentException unknown) {
            // The service rejects an unknown taxpayer this way. Mapped here rather than left to
            // Spring's default because this application has no @ControllerAdvice, so an uncaught
            // IllegalArgumentException is a 500 - which would report a caller's typo as a server
            // fault and page whoever owns the 5xx alert.
            LOG.info("embedding ingest not_found id={} tenant={}: {}", id, tenant, unknown.getMessage());
            return ResponseEntity.notFound().build();
        }

        return ResponseEntity.status(HttpStatus.CREATED).body(Map.of(
                "id", stored.id(),
                "taxpayerId", id,
                "tenant", stored.tenantId(),
                "dimensions", TaxpayerEmbedding.DIMENSIONS));
    }

    /**
     * The taxpayers most similar to this one, within the caller's tenant.
     *
     * <p>This is the read half of the same feature, and it is the only production caller of the
     * HNSW index the migration creates. Without it the vectors would be write-only and the index
     * would be exercised by nothing but a test.
     */
    @GetMapping("/{id}/similar")
    @PreAuthorize(READ_AUTHORITY)
    @Operation(summary = "Find taxpayers similar to this one",
            description = "Cosine nearest-neighbour search over taxcalc.taxpayer_embeddings, scoped "
                    + "to the caller's tenant and served by the HNSW index. Excludes the taxpayer itself.")
    @ApiResponses({
        @ApiResponse(responseCode = "200", description = "Neighbours found (possibly none)"),
        @ApiResponse(responseCode = "400", description = "limit outside 1.." + MAX_LIMIT),
        @ApiResponse(responseCode = "401", description = "Missing or invalid JWT"),
        @ApiResponse(responseCode = "403", description = "JWT present but lacks required scope or role"),
        @ApiResponse(responseCode = "404", description = "This taxpayer has no stored embedding yet")
    })
    public ResponseEntity<Map<String, Object>> similar(@PathVariable String id,
            @RequestParam(name = "limit", defaultValue = "" + DEFAULT_LIMIT) int limit,
            @AuthenticationPrincipal Jwt jwt) {
        if (limit <= 0 || limit > MAX_LIMIT) {
            // Bounded here rather than in the service: the service's own guard rejects a
            // non-positive limit, but an unbounded upper end is an HTTP concern - it lets one
            // request pull every vector the tenant owns.
            LOG.warn("rejected limit={} for id={} (allowed 1..{})", limit, id, MAX_LIMIT);
            return ResponseEntity.badRequest().build();
        }
        String tenant = tenantOf(jwt);
        LOG.info("embedding similar id={} subject={} tenant={} limit={}",
                id, jwt.getSubject(), tenant, limit);

        final List<TaxpayerEmbedding> neighbours;
        try {
            neighbours = ingestService.findSimilar(id, tenant, limit);
        } catch (IllegalArgumentException notIngested) {
            // Not yet embedded is a missing resource, not a bad request: the caller's input is
            // fine, the vector simply does not exist until someone POSTs the ingest route.
            LOG.info("embedding similar not_found id={} tenant={}: {}", id, tenant, notIngested.getMessage());
            return ResponseEntity.notFound().build();
        }

        return ResponseEntity.ok(Map.of(
                "id", id,
                "tenant", tenant,
                "neighbours", neighbours.stream().map(TaxpayerEmbedding::id).toList()));
    }

    /**
     * Resolve the tenant from the JWT's {@code tenant} claim, falling back to {@code shared}.
     *
     * <p>Same rule as {@link TaxpayerController}, and for a sharper reason here: the tenant is the
     * isolation boundary of the search, not just an attribution label. A caller-supplied tenant on
     * this route would let anyone read any tenant's neighbours, so it is taken from the token and
     * nowhere else.
     */
    private static String tenantOf(Jwt jwt) {
        String claim = jwt.getClaimAsString("tenant");
        return claim == null || claim.isBlank() ? "shared" : claim;
    }
}
