package com.uptimecrew.tax_liability.api;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.List;
import java.util.Map;

import com.uptimecrew.tax_liability.embeddings.TaxpayerEmbedding;
import com.uptimecrew.tax_liability.embeddings.TaxpayerEmbeddingIngestService;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.oauth2.jwt.Jwt;

/**
 * The two embedding routes (W6 D4 Task 3), built with {@code new} - no Spring context, no servlet
 * container, milliseconds to run.
 *
 * <p>The service is mocked because what this class is about is the HTTP contract on top of it: the
 * status codes (this application has no {@code @ControllerAdvice}, so anything uncaught is a 500),
 * the bounds on {@code limit}, and above all where the tenant comes from.
 */
class TaxpayerEmbeddingControllerTest {

    private static final String TAXPAYER_ID = "tp-001";

    private TaxpayerEmbeddingIngestService service;
    private TaxpayerEmbeddingController controller;

    @BeforeEach
    void setUp() {
        service = mock(TaxpayerEmbeddingIngestService.class);
        controller = new TaxpayerEmbeddingController(service);
    }

    // -------------------------------------------------------------- POST ingest

    @Test
    void ingestReturns201AndTheStoredRowId() {
        TaxpayerEmbedding stored = new TaxpayerEmbedding(
                "11111111-1111-1111-1111-111111111111", "acme", new float[1024], null);
        when(service.ingest(TAXPAYER_ID, "acme")).thenReturn(stored);

        ResponseEntity<Map<String, Object>> response =
                controller.ingest(TAXPAYER_ID, jwtWithTenant("acme"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(response.getBody()).containsEntry("id", stored.id());
        assertThat(response.getBody()).containsEntry("taxpayerId", TAXPAYER_ID);
        assertThat(response.getBody()).containsEntry("tenant", "acme");
        assertThat(response.getBody()).containsEntry("dimensions", TaxpayerEmbedding.DIMENSIONS);
        // 1024 floats would dominate the payload and tell the caller nothing it can use.
        assertThat(response.getBody()).doesNotContainKey("embedding");
    }

    /**
     * An unknown taxpayer is a 404, not the 500 an uncaught {@link IllegalArgumentException} would
     * produce - that would report a caller's typo as a server fault and page whoever owns the 5xx
     * alert.
     */
    @Test
    void ingestReturns404ForAnUnknownTaxpayer() {
        when(service.ingest(anyString(), anyString()))
                .thenThrow(new IllegalArgumentException("unknown id nope"));

        assertThat(controller.ingest("nope", jwtWithTenant("acme")).getStatusCode())
                .isEqualTo(HttpStatus.NOT_FOUND);
    }

    // ------------------------------------------------------------- GET similar

    @Test
    void similarReturnsNeighbourIdsNearestFirst() {
        when(service.findSimilar(TAXPAYER_ID, "acme", 2)).thenReturn(List.of(
                new TaxpayerEmbedding("11111111-1111-1111-1111-111111111111", "acme", new float[1024], null),
                new TaxpayerEmbedding("22222222-2222-2222-2222-222222222222", "acme", new float[1024], null)));

        ResponseEntity<Map<String, Object>> response =
                controller.similar(TAXPAYER_ID, 2, jwtWithTenant("acme"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).containsEntry("neighbours", List.of(
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222"));
    }

    @Test
    void similarReturns404WhenTheTaxpayerHasNoStoredEmbedding() {
        when(service.findSimilar(anyString(), anyString(), anyInt()))
                .thenThrow(new IllegalArgumentException("no stored embedding"));

        assertThat(controller.similar(TAXPAYER_ID, 5, jwtWithTenant("acme")).getStatusCode())
                .isEqualTo(HttpStatus.NOT_FOUND);
    }

    /**
     * The upper bound is the point: without it one request can pull every vector a tenant owns.
     * Rejected before the service is called, so a hostile limit never reaches the database.
     */
    @Test
    void similarRejectsALimitOutsideTheAllowedRange() {
        Jwt jwt = jwtWithTenant("acme");

        assertThat(controller.similar(TAXPAYER_ID, 0, jwt).getStatusCode())
                .isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(controller.similar(TAXPAYER_ID, -1, jwt).getStatusCode())
                .isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(controller.similar(TAXPAYER_ID,
                TaxpayerEmbeddingController.MAX_LIMIT + 1, jwt).getStatusCode())
                .isEqualTo(HttpStatus.BAD_REQUEST);

        verify(service, never()).findSimilar(anyString(), anyString(), anyInt());
    }

    @Test
    void similarAcceptsTheBoundariesOfTheAllowedRange() {
        when(service.findSimilar(anyString(), anyString(), anyInt())).thenReturn(List.of());
        Jwt jwt = jwtWithTenant("acme");

        assertThat(controller.similar(TAXPAYER_ID, 1, jwt).getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(controller.similar(TAXPAYER_ID,
                TaxpayerEmbeddingController.MAX_LIMIT, jwt).getStatusCode()).isEqualTo(HttpStatus.OK);
    }

    // ---------------------------------------------------------------- tenancy

    /**
     * The tenant comes from the token and from nowhere else. It is the isolation boundary of the
     * search, so a caller-supplied tenant would let anyone read any tenant's neighbours.
     */
    @Test
    void bothRoutesTakeTheTenantFromTheToken() {
        when(service.ingest(anyString(), anyString())).thenReturn(
                new TaxpayerEmbedding("11111111-1111-1111-1111-111111111111", "globex", new float[1024], null));
        when(service.findSimilar(anyString(), anyString(), anyInt())).thenReturn(List.of());

        controller.ingest(TAXPAYER_ID, jwtWithTenant("globex"));
        controller.similar(TAXPAYER_ID, 5, jwtWithTenant("globex"));

        verify(service).ingest(TAXPAYER_ID, "globex");
        verify(service).findSimilar(TAXPAYER_ID, "globex", 5);
    }

    /**
     * A missing claim degrades to {@code shared} rather than failing the request - the same rule
     * the LLM routes use, so one token works across both.
     */
    @Test
    void aTokenWithNoTenantClaimFallsBackToShared() {
        when(service.ingest(anyString(), anyString())).thenReturn(
                new TaxpayerEmbedding("11111111-1111-1111-1111-111111111111", "shared", new float[1024], null));

        controller.ingest(TAXPAYER_ID, jwtWithoutTenant());

        verify(service).ingest(eq(TAXPAYER_ID), eq("shared"));
    }

    @Test
    void rejectsANullService() {
        assertThrows(NullPointerException.class, () -> new TaxpayerEmbeddingController(null));
    }

    private static Jwt jwtWithTenant(String tenant) {
        return Jwt.withTokenValue("token")
                .header("alg", "none")
                .subject("user-1")
                .claim("tenant", tenant)
                .build();
    }

    private static Jwt jwtWithoutTenant() {
        return Jwt.withTokenValue("token")
                .header("alg", "none")
                .subject("user-1")
                .claim("scope", "taxpayers.write")
                .build();
    }
}
