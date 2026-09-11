package com.uptimecrew.tax_liability.embeddings;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.StringJoiner;
import java.util.UUID;

import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * The ingest path (W6 D4 Task 3): taxpayer text in, a stored 1024-dimension vector out.
 *
 * <p>This is the seam the rest of the feature was missing. {@link EmbeddingsClient} could embed
 * and {@link TaxpayerEmbeddingRepository} could store, but nothing in production code joined them,
 * so no request could ever put a vector in the table - the schema, the index and the client were
 * all reachable only from tests. This class is the single production path from a taxpayer record
 * to a row in {@code taxcalc.taxpayer_embeddings}, and {@code TaxpayerEmbeddingController} is its
 * only caller.
 *
 * <h2>The row id is derived, not random</h2>
 *
 * <p>The table the task specifies has four columns and none of them is a taxpayer id, so the
 * primary key has to carry that link itself. {@link #rowId} derives a UUID from
 * {@code tenant|taxpayerId}, which buys three things a random v4 id would not:
 *
 * <ul>
 *   <li>re-embedding a taxpayer <em>replaces</em> its vector instead of accumulating a new row on
 *       every call - see {@link TaxpayerEmbeddingRepository#save} for the upsert;
 *   <li>the row for a taxpayer can be found again without a second lookup table, which is what
 *       {@link #findSimilar} uses;
 *   <li>the tenant is part of the derivation, so the same taxpayer id under two tenants is two
 *       rows rather than one tenant silently overwriting the other's vector.
 * </ul>
 *
 * <h2>What it costs</h2>
 *
 * <p>Nothing per call. The model runs in the cluster ({@code embeddings.taxcalc-dev.svc}), so
 * unlike {@link com.uptimecrew.tax_liability.llm.LiabilityExplanationService} this path needs no
 * cost middleware and writes no cost line - its resource envelope is the Deployment's CPU request,
 * not an invoice. That is why the ingest endpoint is not behind the LLM rate limiter either.
 */
@Service
public class TaxpayerEmbeddingIngestService {

    private static final Logger LOG = LoggerFactory.getLogger(TaxpayerEmbeddingIngestService.class);

    private final TaxpayerReadModelRepository readModelRepository;
    private final EmbeddingsClient embeddingsClient;
    private final TaxpayerEmbeddingRepository embeddingRepository;

    /**
     * @param readModelRepository  source of the taxpayer text to embed; never null
     * @param embeddingsClient     the in-cluster TEI client; never null
     * @param embeddingRepository  pgvector storage; never null
     * @throws NullPointerException if any argument is null
     */
    public TaxpayerEmbeddingIngestService(TaxpayerReadModelRepository readModelRepository,
            EmbeddingsClient embeddingsClient, TaxpayerEmbeddingRepository embeddingRepository) {
        this.readModelRepository =
                Objects.requireNonNull(readModelRepository, "readModelRepository must not be null");
        this.embeddingsClient =
                Objects.requireNonNull(embeddingsClient, "embeddingsClient must not be null");
        this.embeddingRepository =
                Objects.requireNonNull(embeddingRepository, "embeddingRepository must not be null");
    }

    /**
     * Embed one taxpayer's record and store the vector.
     *
     * <p>Reads the taxpayer, renders it as text ({@link #embeddableText}), POSTs that to the
     * in-cluster embeddings model, and upserts the returned 1024-vector.
     *
     * @param taxpayerId the taxpayer to embed; never null
     * @param tenantId   the owning tenant, which scopes every later search; never null or blank
     * @return the stored row, with the vector that was written
     * @throws NullPointerException     if either argument is null
     * @throws IllegalArgumentException if no taxpayer has that id, or {@code tenantId} is blank
     * @throws IllegalStateException    if the embeddings service returns nothing usable, or a
     *                                  vector whose dimension does not match the column
     */
    public TaxpayerEmbedding ingest(String taxpayerId, String tenantId) {
        Objects.requireNonNull(taxpayerId, "taxpayerId must not be null");
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        if (tenantId.isBlank()) {
            throw new IllegalArgumentException("tenantId must not be blank");
        }

        TaxpayerReadModel taxpayer = readModelRepository.findById(taxpayerId)
                .orElseThrow(() -> new IllegalArgumentException("unknown id " + taxpayerId));

        String text = embeddableText(taxpayer);
        float[] vector = embeddingsClient.embed(text);

        TaxpayerEmbedding row = new TaxpayerEmbedding(rowId(tenantId, taxpayerId), tenantId, vector, null);
        embeddingRepository.save(row);

        LOG.info("embedding ingested taxpayerId={} tenant={} rowId={} chars={} dims={}",
                taxpayerId, tenantId, row.id(), text.length(), vector.length);
        return row;
    }

    /**
     * The {@code limit} taxpayers whose stored vectors are nearest this one's, within the same
     * tenant.
     *
     * <p>Queries with the taxpayer's <em>already stored</em> vector rather than re-embedding its
     * text: the text has not changed since ingest, so a second model call would spend cluster CPU
     * to reproduce a vector already in the row - and, if the model were swapped in between, would
     * compare vectors from two different models, which is meaningless rather than merely slow.
     *
     * <p>The taxpayer's own row is the nearest match to itself at distance 0 and is dropped, so a
     * caller asking for 5 neighbours gets 5 other taxpayers.
     *
     * @param taxpayerId the taxpayer to search around; never null
     * @param tenantId   the tenant to search within; never null or blank
     * @param limit      how many neighbours to return; must be positive
     * @return the neighbours, nearest first, never including {@code taxpayerId} itself
     * @throws NullPointerException     if either id is null
     * @throws IllegalArgumentException if {@code tenantId} is blank, {@code limit} is not
     *                                  positive, or this taxpayer has never been ingested
     */
    public List<TaxpayerEmbedding> findSimilar(String taxpayerId, String tenantId, int limit) {
        Objects.requireNonNull(taxpayerId, "taxpayerId must not be null");
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        if (tenantId.isBlank()) {
            throw new IllegalArgumentException("tenantId must not be blank");
        }
        if (limit <= 0) {
            throw new IllegalArgumentException("limit must be positive, was " + limit);
        }

        String id = rowId(tenantId, taxpayerId);
        List<TaxpayerEmbedding> self = embeddingRepository.findById(id);
        if (self.isEmpty()) {
            throw new IllegalArgumentException("taxpayer " + taxpayerId
                    + " has no stored embedding for tenant " + tenantId
                    + " - POST /api/v1/taxpayers/" + taxpayerId + "/embedding first");
        }

        // limit + 1, because the nearest row is always this taxpayer's own at distance 0.
        List<TaxpayerEmbedding> nearest =
                embeddingRepository.findNearest(tenantId, self.get(0).embedding(), limit + 1);

        List<TaxpayerEmbedding> neighbours = new ArrayList<>(nearest.size());
        for (TaxpayerEmbedding candidate : nearest) {
            if (!candidate.id().equals(id)) {
                neighbours.add(candidate);
            }
        }
        // Asking for limit + 1 and removing one leaves limit rows - unless the self row was not in
        // the result at all (an approximate index is allowed to miss it), in which case trim.
        return neighbours.size() > limit ? List.copyOf(neighbours.subList(0, limit)) : List.copyOf(neighbours);
    }

    /**
     * Render a taxpayer read model as the text that gets embedded.
     *
     * <p>Package-private and deterministic so it can be asserted without a model call. Two
     * properties matter and are tested:
     *
     * <ul>
     *   <li><b>It is stable.</b> The same record must produce the same string on every call, or
     *       the same taxpayer embeds to a different vector each time and nearest-neighbour results
     *       drift for reasons nothing in the data explains.
     *   <li><b>It carries the fields a similarity question is actually about</b> - filing status,
     *       jurisdiction, and the liability figures - and not the display name alone, which would
     *       cluster taxpayers by how their names are spelled.
     * </ul>
     *
     * <p>Amounts are rendered from the record's {@code BigDecimal}s via {@code toString}, not
     * reformatted: this text is model input, never a figure anybody is charged, and re-rounding it
     * here would put a second rounding policy next to the one in
     * {@link com.uptimecrew.tax_liability.service.TaxLiabilityService}.
     */
    String embeddableText(TaxpayerReadModel taxpayer) {
        Objects.requireNonNull(taxpayer, "taxpayer must not be null");

        StringJoiner text = new StringJoiner("; ");
        text.add("taxpayer " + taxpayer.getId());
        text.add("filing status " + valueOrUnknown(taxpayer.getFilingStatus()));
        text.add("jurisdiction " + valueOrUnknown(taxpayer.getHomeJurisdiction()));

        List<TaxpayerReadModel.EmbeddedLiability> liabilities = taxpayer.getLiabilities();
        if (liabilities == null || liabilities.isEmpty()) {
            text.add("no recorded liabilities");
        } else {
            for (TaxpayerReadModel.EmbeddedLiability liability : liabilities) {
                text.add("tax year " + liability.getTaxYear()
                        + " bracket " + liability.getBracketId()
                        + " taxable " + liability.getTaxableAmount()
                        + " liability " + liability.getLiabilityAmount());
            }
        }

        List<String> tags = taxpayer.getTags();
        if (tags != null && !tags.isEmpty()) {
            text.add("tags " + String.join(",", tags));
        }
        return text.toString();
    }

    /**
     * The deterministic row id for a taxpayer under a tenant.
     *
     * <p>{@link UUID#nameUUIDFromBytes} is a name-based (v3) UUID: same input, same id, forever
     * and on every node - which is what makes ingest idempotent without a lookup table. It is not
     * a security boundary and is not meant to be one; it is a stable naming scheme for a primary
     * key whose column type is {@code UUID}.
     *
     * <p>The separator is {@code '|'}, a character neither a tenant nor a taxpayer id contains, so
     * {@code ("ab", "c")} and {@code ("a", "bc")} cannot collide into one row.
     */
    static String rowId(String tenantId, String taxpayerId) {
        return UUID.nameUUIDFromBytes(
                (tenantId + '|' + taxpayerId).getBytes(StandardCharsets.UTF_8)).toString();
    }

    /** Never embeds the literal string "null", which would be a token the model has to interpret. */
    private static String valueOrUnknown(String value) {
        return value == null || value.isBlank() ? "unknown" : value;
    }
}
