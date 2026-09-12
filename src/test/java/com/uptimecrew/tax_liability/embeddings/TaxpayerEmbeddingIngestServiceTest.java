package com.uptimecrew.tax_liability.embeddings;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

/**
 * The ingest path, without a database and without an embeddings service (W6 D4 Task 3).
 *
 * <p>Both collaborators are mocked deliberately. The real ones are covered where they can be
 * covered honestly - {@link TaxpayerEmbeddingsRepoTest} runs the SQL against real Postgres,
 * {@link EmbeddingsClientLiveIT} runs the client against real TEI - and neither is what this class
 * is about. What this class asserts is the wiring those two tests cannot see: that a taxpayer is
 * turned into text, that the text reaches the model, that the model's vector is what gets stored,
 * and that the row id is derived rather than random.
 */
class TaxpayerEmbeddingIngestServiceTest {

    private static final String TENANT = "acme";
    private static final String TAXPAYER_ID = "tp-001";

    private TaxpayerReadModelRepository readModels;
    private EmbeddingsClient client;
    private TaxpayerEmbeddingRepository embeddings;
    private TaxpayerEmbeddingIngestService service;

    @BeforeEach
    void setUp() {
        readModels = mock(TaxpayerReadModelRepository.class);
        client = mock(EmbeddingsClient.class);
        embeddings = mock(TaxpayerEmbeddingRepository.class);
        service = new TaxpayerEmbeddingIngestService(readModels, client, embeddings);
    }

    // ------------------------------------------------------------- the wiring

    @Test
    void embedsTheTaxpayerTextAndStoresTheReturnedVector() {
        TaxpayerReadModel taxpayer = taxpayer();
        when(readModels.findById(TAXPAYER_ID)).thenReturn(Optional.of(taxpayer));
        float[] modelOutput = axis(11);
        when(client.embed(anyString())).thenReturn(modelOutput);

        TaxpayerEmbedding stored = service.ingest(TAXPAYER_ID, TENANT);

        // The text the model was asked to embed is the text built from the record - asserted on
        // the captured argument, because a service that embedded some other string would still
        // return a perfectly valid-looking vector.
        ArgumentCaptor<String> text = ArgumentCaptor.forClass(String.class);
        verify(client).embed(text.capture());
        assertThat(text.getValue()).contains(TAXPAYER_ID).contains("single").contains("CA");

        ArgumentCaptor<TaxpayerEmbedding> saved = ArgumentCaptor.forClass(TaxpayerEmbedding.class);
        verify(embeddings).save(saved.capture());
        assertThat(saved.getValue().embedding()).isEqualTo(modelOutput);
        assertThat(saved.getValue().tenantId()).isEqualTo(TENANT);
        assertThat(stored).isEqualTo(saved.getValue());
    }

    /**
     * The point of deriving the id: a second ingest of the same taxpayer writes the SAME row, so
     * re-embedding after a change replaces the vector instead of leaving a stale one behind that a
     * search would keep returning alongside the new one.
     */
    @Test
    void reIngestingTheSameTaxpayerReusesTheSameRowId() {
        when(readModels.findById(TAXPAYER_ID)).thenReturn(Optional.of(taxpayer()));
        when(client.embed(anyString())).thenReturn(axis(1), axis(2));

        String first = service.ingest(TAXPAYER_ID, TENANT).id();
        String second = service.ingest(TAXPAYER_ID, TENANT).id();

        assertThat(second).isEqualTo(first);
    }

    /** Same taxpayer id under two tenants is two rows - one tenant must not overwrite the other. */
    @Test
    void theSameTaxpayerUnderTwoTenantsGetsTwoRows() {
        when(readModels.findById(TAXPAYER_ID)).thenReturn(Optional.of(taxpayer()));
        when(client.embed(anyString())).thenReturn(axis(1));

        assertThat(service.ingest(TAXPAYER_ID, "acme").id())
                .isNotEqualTo(service.ingest(TAXPAYER_ID, "globex").id());
    }

    /** The '|' separator exists so two different pairs cannot concatenate to one id. */
    @Test
    void derivedIdsCannotCollideAcrossTheTenantBoundary() {
        assertThat(TaxpayerEmbeddingIngestService.rowId("ab", "c"))
                .isNotEqualTo(TaxpayerEmbeddingIngestService.rowId("a", "bc"));
    }

    @Test
    void storesNothingWhenTheTaxpayerIsUnknown() {
        when(readModels.findById("missing")).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.ingest("missing", TENANT))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("missing");

        verify(client, never()).embed(anyString());
        verify(embeddings, never()).save(any());
    }

    /**
     * A wrong-dimension vector from the model must not reach the database. The client raises it
     * first; this asserts the service does not swallow it and store a broken row.
     */
    @Test
    void doesNotStoreAVectorTheModelSizedWrongly() {
        when(readModels.findById(TAXPAYER_ID)).thenReturn(Optional.of(taxpayer()));
        when(client.embed(anyString())).thenReturn(new float[512]);

        assertThatThrownBy(() -> service.ingest(TAXPAYER_ID, TENANT))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("1024");

        verify(embeddings, never()).save(any());
    }

    // ------------------------------------------------------------ the text

    @Test
    void theEmbeddedTextIsStableForTheSameRecord() {
        TaxpayerReadModel taxpayer = taxpayer();

        assertThat(service.embeddableText(taxpayer)).isEqualTo(service.embeddableText(taxpayer));
    }

    @Test
    void theEmbeddedTextCarriesTheLiabilityFiguresAndTags() {
        String text = service.embeddableText(taxpayer());

        assertThat(text)
                .contains("tax year 2024")
                .contains("bracket br-fed-2024")
                .contains("taxable 120000.00")
                .contains("liability 18000.00")
                .contains("tags vip");
    }

    /** A record with no liabilities still produces text, rather than an empty string to embed. */
    @Test
    void handlesATaxpayerWithNoLiabilities() {
        TaxpayerReadModel bare = new TaxpayerReadModel(
                "tp-bare", "Nobody", "single", "CA", Instant.EPOCH, List.of());

        assertThat(service.embeddableText(bare)).contains("no recorded liabilities");
    }

    /**
     * Null fields, which the constructor forbids but Mongo produces: a document written before a
     * field existed simply has no key for it, and Spring Data populates the field reflectively,
     * leaving it null. That is exactly the case {@code TaxpayerReadModel}'s own javadoc describes
     * for {@code tags}, so it is reachable in production and is mocked here because no constructor
     * can build it.
     */
    @Test
    void rendersNullFieldsAsUnknownRatherThanTheStringNull() {
        TaxpayerReadModel sparse = mock(TaxpayerReadModel.class);
        when(sparse.getId()).thenReturn("tp-sparse");
        when(sparse.getFilingStatus()).thenReturn(null);
        when(sparse.getHomeJurisdiction()).thenReturn("   ");
        when(sparse.getLiabilities()).thenReturn(null);
        when(sparse.getTags()).thenReturn(null);

        String text = service.embeddableText(sparse);

        assertThat(text)
                .contains("filing status unknown")
                .contains("jurisdiction unknown")
                .contains("no recorded liabilities")
                // The literal "null" would be a token the model has to interpret as content.
                .doesNotContain("null");
    }

    // ------------------------------------------------------------- the search

    @Test
    void findSimilarQueriesWithTheStoredVectorAndDropsTheTaxpayerItself() {
        String selfId = TaxpayerEmbeddingIngestService.rowId(TENANT, TAXPAYER_ID);
        TaxpayerEmbedding self = new TaxpayerEmbedding(selfId, TENANT, axis(3), null);
        TaxpayerEmbedding neighbour = new TaxpayerEmbedding(
                "11111111-1111-1111-1111-111111111111", TENANT, axis(4), null);
        when(embeddings.findById(selfId)).thenReturn(List.of(self));
        when(embeddings.findNearest(eq(TENANT), any(float[].class), anyInt()))
                .thenReturn(List.of(self, neighbour));

        List<TaxpayerEmbedding> found = service.findSimilar(TAXPAYER_ID, TENANT, 1);

        assertThat(found).containsExactly(neighbour);
        // No second model call: the stored vector is reused, so this path costs no cluster CPU.
        verify(client, never()).embed(anyString());
        // limit + 1 is requested, because the self row occupies the first slot.
        verify(embeddings).findNearest(TENANT, axis(3), 2);
    }

    /** With no self row in the result, the caller still gets no more than it asked for. */
    @Test
    void findSimilarTrimsToTheRequestedLimit() {
        String selfId = TaxpayerEmbeddingIngestService.rowId(TENANT, TAXPAYER_ID);
        when(embeddings.findById(selfId))
                .thenReturn(List.of(new TaxpayerEmbedding(selfId, TENANT, axis(3), null)));
        when(embeddings.findNearest(eq(TENANT), any(float[].class), anyInt())).thenReturn(List.of(
                new TaxpayerEmbedding("11111111-1111-1111-1111-111111111111", TENANT, axis(4), null),
                new TaxpayerEmbedding("22222222-2222-2222-2222-222222222222", TENANT, axis(5), null)));

        assertThat(service.findSimilar(TAXPAYER_ID, TENANT, 1)).hasSize(1);
    }

    @Test
    void findSimilarRejectsATaxpayerThatWasNeverIngested() {
        when(embeddings.findById(anyString())).thenReturn(List.of());

        assertThatThrownBy(() -> service.findSimilar(TAXPAYER_ID, TENANT, 5))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("no stored embedding");
    }

    // ----------------------------------------------------------- the contracts

    @Test
    void rejectsNullsAndBlanks() {
        assertThrows(NullPointerException.class, () -> service.ingest(null, TENANT));
        assertThrows(NullPointerException.class, () -> service.ingest(TAXPAYER_ID, null));
        assertThrows(IllegalArgumentException.class, () -> service.ingest(TAXPAYER_ID, "  "));

        assertThrows(NullPointerException.class, () -> service.findSimilar(null, TENANT, 5));
        assertThrows(NullPointerException.class, () -> service.findSimilar(TAXPAYER_ID, null, 5));
        assertThrows(IllegalArgumentException.class, () -> service.findSimilar(TAXPAYER_ID, "  ", 5));
        assertThrows(IllegalArgumentException.class, () -> service.findSimilar(TAXPAYER_ID, TENANT, 0));
    }

    @Test
    void rejectsNullConstructorArguments() {
        assertThrows(NullPointerException.class,
                () -> new TaxpayerEmbeddingIngestService(null, client, embeddings));
        assertThrows(NullPointerException.class,
                () -> new TaxpayerEmbeddingIngestService(readModels, null, embeddings));
        assertThrows(NullPointerException.class,
                () -> new TaxpayerEmbeddingIngestService(readModels, client, null));
        assertThrows(NullPointerException.class, () -> service.embeddableText(null));
    }

    private static TaxpayerReadModel taxpayer() {
        TaxpayerReadModel taxpayer = new TaxpayerReadModel(
                TAXPAYER_ID, "Ada Lovelace", "single", "CA", Instant.EPOCH,
                List.of(new TaxpayerReadModel.EmbeddedLiability(
                        2024, "br-fed-2024",
                        new BigDecimal("120000.00"), new BigDecimal("18000.00"), Instant.EPOCH)),
                List.of("vip"));
        return taxpayer;
    }

    /** An axis-aligned unit vector, for the same reason {@link TaxpayerEmbeddingsRepoTest} uses one. */
    private static float[] axis(int i) {
        float[] v = new float[TaxpayerEmbedding.DIMENSIONS];
        v[i] = 1.0f;
        return v;
    }
}
