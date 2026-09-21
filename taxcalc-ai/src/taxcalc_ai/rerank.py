# taxcalc-ai/src/taxcalc_ai/rerank.py
"""MMR diversification, then cross-encoder reranking under a strict timeout-and-fallback.

Two stages, in this order, and the order is not interchangeable.

**MMR first, because the reranker is expensive per candidate.** RRF's top-60 is a recall-
oriented list and it is redundant: near-duplicate chunks - the same paragraph from two document
versions, an overlap window shared by adjacent chunks - all score well on the same query, so the
head of the list can be five restatements of one fact. Feeding that to a cross-encoder pays full
price to rerank five copies of the same passage and then hands the generator a context window
with one fact in it. Maximal Marginal Relevance picks greedily, subtracting a penalty for
similarity to what it has already picked, so 60 candidates become 20 that actually differ.

**Then the cross-encoder, because it is the only stage that reads the query and the passage
together.** A bi-encoder (what the retrieval uses) embeds the query and the passage
independently and compares vectors; it never sees them in the same forward pass, so it cannot
represent "this passage answers *that* question". A cross-encoder scores the pair jointly. That
is a materially better relevance signal and it costs a full transformer forward pass per
candidate, which is why it runs last, on 20 rows, and never on 60.

**The 300 ms timeout is wired in code, not assumed from a config file, and it FAILS SOFT.**
This is the deviation from a normal timeout most worth understanding. Reranking is a *quality*
improvement on an ordering that is already correct-ish: the retrieval-order list is a usable
answer. So when the reranker overruns, the right behaviour is to return the retrieval order and
carry on - raising would convert a slow reranker into a failed request, which is a strictly
worse outcome for the user and a self-inflicted outage when the model server is merely warm.
What must NOT happen is the overrun passing unnoticed, so every rerank attempt increments the
``rerank_timeout``/``rerank_requests`` counters in :mod:`taxcalc_ai.metrics` (scrapeable, and the
thing an alert rule actually reads), writes the breach onto its own LangSmith span, and returns a
``rerank_timed_out`` boolean the caller can propagate. An SRE alerts on the counter ratio; nobody
gets paged for a 400 ms p99.

**The check is post-hoc, and that is a real limitation, stated rather than hidden.**
``CrossEncoder.predict`` is a synchronous blocking call into PyTorch with no cancellation seam,
so this measures the elapsed time *after* the call returns and reports the breach. It bounds the
damage to one request's visibility, not one request's latency. Genuinely capping the wall clock
means moving the model behind a process boundary that can be abandoned - a subprocess or an
inference server with its own deadline - which is a deployment change, not a code change, and
is the right next step once the metric shows it is needed.

**300 ms is a budget for accelerated inference, and on CPU it will breach - by design.**
Measured on a GitHub shared runner, eight ``(query, passage)`` pairs alone exceed 300 ms; the
production path sends twenty. So on CPU-only hardware this stage falls back to retrieval order
most of the time, and the ``rerank_timeout`` metric will read close to 100%. That is the correct
behaviour rather than a misconfiguration: the deadline is a statement about the latency the
product can afford, not about what the current hardware can deliver, and the soft failure is
what keeps the mismatch a quality degradation instead of an outage. The metric is the signal
that the stage needs a GPU or a dedicated inference server to pay for itself - which is exactly
the decision it exists to inform. Lowering the ambition to whatever CPU happens to manage would
hide that.

**Two backends, one call site, chosen by the ``RERANKER`` environment variable.**
:func:`rerank_candidates` is the pipeline's only rerank entry point; it dispatches to
:func:`bge_rerank` (the local cross-encoder described above) or :func:`cohere_rerank` (hosted
``rerank-3.5``). The two are signature- and contract-compatible on purpose - same tuple shape, same
soft-failure semantics, same counters - so the choice is genuinely a choice and not a fork in the
pipeline. The trade is: bge costs 1.1 GB of resident weights, a CPU forward pass per candidate and
a deadline it can only measure after the fact; Cohere costs a network hop and per-query billing and
buys a deadline that actually cancels. An unrecognised value raises rather than defaulting - see
:func:`resolve_reranker` for why that is the opposite of the neighbouring stage flags.

**Input length is capped by tokens, not by document count.** ``max_length=256`` on the model
plus a character slice on each passage: a cross-encoder's cost is quadratic in sequence length,
so a single 8 KB chunk costs more than thirty short ones. Capping the candidate *count* alone
leaves the latency budget at the mercy of one long document.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Final

import httpx
import numpy as np
from langsmith import get_current_run_tree, traceable
from numpy.typing import NDArray
from sentence_transformers import CrossEncoder, SentenceTransformer

from taxcalc_ai.metrics import record_rerank

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.rerank")

#: The cross-encoder. A reranker, not an embedder: it consumes ``(query, passage)`` pairs and
#: emits a scalar relevance score, and it has no usable vector output at all.
RERANKER_MODEL: Final[str] = "BAAI/bge-reranker-base"

#: Environment variable choosing which reranker the pipeline uses. Unprefixed on purpose, which
#: is the one deviation from the ``RAG_USE_*`` / ``TAXCALC_*`` naming around it: three processes
#: read this one decision - this sidecar, the W7 D4 MCP server that embeds it, and the W7 D5 agent
#: service that sets it - and a per-service prefix would make one decision into three variables
#: that have to be kept equal by hand. That is precisely the failure this constant closes: the
#: agent service used to export ``TAXCALC_AI_RERANKER``, nothing here read it, and setting it to
#: Cohere silently changed nothing at all.
RERANKER_ENV: Final[str] = "RERANKER"

#: The two accepted values of :data:`RERANKER_ENV`. Short tokens rather than model ids, because
#: the model id is each backend's implementation detail - Cohere's moves with their releases, and
#: the local one is a HuggingFace path - while the decision an operator actually makes is "local
#: encoder or hosted API".
RERANKER_BGE: Final[str] = "bge"
RERANKER_COHERE: Final[str] = "cohere"

#: Cohere's reranker, pinned to an exact model rather than a moving alias: a hosted model that
#: changes under this repository's pinned RAGAS floors turns a vendor release into a CI failure
#: nobody made and nobody can bisect.
COHERE_RERANK_MODEL: Final[str] = "rerank-3.5"

#: Cohere's rerank endpoint. v2, which takes ``documents`` as plain strings and returns
#: ``{index, relevance_score}`` - v1 took a ``documents`` list of objects and is deprecated.
COHERE_RERANK_URL: Final[str] = "https://api.cohere.com/v2/rerank"

#: Cohere credential. Read straight from the environment rather than threaded through this
#: module's signatures, for the same reason the pipeline's other clients are: the caller that
#: selects a backend should not also have to know what that backend authenticates with.
COHERE_API_KEY_ENV: Final[str] = "COHERE_API_KEY"

#: Joint sequence length the reranker truncates pairs to. Cost is quadratic in this number.
RERANKER_MAX_LENGTH: Final[int] = 256

#: The strict budget, in milliseconds. Breaching it returns the retrieval order and flags the
#: request rather than raising - see the module docstring.
RERANK_TIMEOUT_MS: Final[int] = 300

#: Character cap applied to each passage before pairing. ~4 chars/token against
#: :data:`RERANKER_MAX_LENGTH` leaves headroom for the query; the model truncates anyway, and
#: slicing first means the tokeniser never walks an 8 KB string to throw most of it away.
PASSAGE_CHAR_CAP: Final[int] = 1024

#: MMR trade-off. 0.7 weights query relevance over novelty: at 1.0 MMR degenerates to plain
#: cosine top-k (no diversification at all), at 0.0 it ignores the query and returns the most
#: mutually-dissimilar chunks it can find, which is a different and useless thing.
MMR_LAMBDA: Final[float] = 0.7

#: Candidates MMR keeps out of the fused list.
DEFAULT_MMR_K: Final[int] = 20

#: Candidates the reranker returns - the context window handed to the generator.
DEFAULT_RERANK_TOP_K: Final[int] = 6

#: Span attribute and metric name the timeout is reported under. A contract with whatever
#: dashboards and alerts consume the LangSmith project, so it is named once here - and the same
#: string names the Prometheus counter family in :mod:`taxcalc_ai.metrics`, so the trace
#: attribute and the alertable series cannot drift apart.
RERANK_TIMEOUT_ATTRIBUTE: Final[str] = "rerank_timeout"

#: Module-level cache. ``bge-reranker-base`` is ~1.1 GB on disk and several seconds to
#: construct; building it per call would make the timeout above unmeetable by construction and
#: would attribute the model load to whichever request was unlucky. Cached per process, so a
#: worker pays once at its first rerank.
_RERANKER: CrossEncoder | None = None


def _get_reranker() -> CrossEncoder:
    """Return the process-wide reranker, constructing it on first use.

    Deliberately lazy rather than loaded at import: a process that never reranks - the Airflow
    ingest DAG, the chunker tests - should not pay 1.1 GB of weights to import a module.

    :returns: The cached :class:`~sentence_transformers.CrossEncoder`.
    """
    global _RERANKER
    if _RERANKER is None:
        _RERANKER = CrossEncoder(RERANKER_MODEL, max_length=RERANKER_MAX_LENGTH)
    return _RERANKER


@traceable(run_type="chain", name="taxcalc_ai.mmr_pick")
def mmr_pick(
    query_vec: NDArray[np.float32],
    candidates: list[tuple[str, str, float]],
    embedder: SentenceTransformer,
    k: int = DEFAULT_MMR_K,
    lambda_param: float = MMR_LAMBDA,
) -> list[tuple[str, str, float]]:
    """Greedily pick ``k`` candidates maximising relevance minus redundancy.

    The objective at each step is
    ``lambda * sim(query, candidate) - (1 - lambda) * max sim(candidate, already_picked)``.
    The first pick has nothing to compare against, so it is the plain nearest candidate; every
    pick after it is discounted by how much it resembles the best-matching thing already chosen.

    Candidates are re-embedded here with the bi-encoder rather than reusing the retrieval
    vectors. That is a deliberate cost: the candidate-to-candidate similarities MMR needs were
    never computed by the retrieval (which only measured candidate-to-query), and the sparse
    path contributes candidates that have no query-relative score at all. Encoding all of them
    once in a single batched call is cheaper than a second database round trip for vectors.

    Unit-normalised vectors are requested from the encoder, which is what lets the dot product
    stand in for cosine similarity - so the similarity matrix is one matrix multiply rather
    than a per-pair normalisation.

    :param query_vec: The query embedding, ``float32`` and unit length.
    :param candidates: Fused candidates as ``(chunk_id, chunk_text, score)``. The incoming score
        is carried through untouched, not replaced by the MMR objective: the objective is a
        selection criterion over *this* candidate set and has no meaning outside it, whereas the
        RRF score is what a caller may want to inspect.
    :param embedder: The bi-encoder the corpus was embedded with.
    :param k: How many candidates to keep. Fewer candidates than ``k`` returns all of them.
    :param lambda_param: Relevance/novelty trade-off in ``[0, 1]``; see :data:`MMR_LAMBDA`.
    :returns: The selected candidates in pick order - which is a relevance-and-diversity order,
        not the retrieval order.
    """
    if not candidates:
        return []

    texts = [text for _, text, _ in candidates]
    cand_vecs: NDArray[np.float32] = embedder.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)
    query = query_vec.astype(np.float32)
    # Unit vectors, so the dot product IS cosine similarity - larger is better here, the
    # opposite polarity from the cosine *distance* the SQL returns.
    sim_to_query: NDArray[np.float32] = cand_vecs @ query

    picked: list[int] = []
    remaining = list(range(len(candidates)))
    while remaining and len(picked) < k:
        best_idx = -1
        best_score = -np.inf
        for i in remaining:
            if not picked:
                score = float(sim_to_query[i])
            else:
                # Max similarity to ANY already-picked candidate, not the mean: a chunk that
                # duplicates one earlier pick is redundant regardless of how unlike the others
                # it is, and a mean would let it through by averaging that away.
                max_sim = float(np.max(cand_vecs[picked] @ cand_vecs[i]))
                score = lambda_param * float(sim_to_query[i]) - (1.0 - lambda_param) * max_sim
            if score > best_score:
                best_score = score
                best_idx = i
        picked.append(best_idx)
        remaining.remove(best_idx)

    _LOG.info(
        "rerank.mmr.selected",
        extra={
            "event": "rerank.mmr.selected",
            "candidates": len(candidates),
            "selected": len(picked),
            "lambda": lambda_param,
        },
    )
    return [candidates[i] for i in picked]


@traceable(run_type="chain", name="taxcalc_ai.bge_rerank")
def bge_rerank(
    query_text: str,
    candidates: list[tuple[str, str, float]],
    top_k: int = DEFAULT_RERANK_TOP_K,
    timeout_ms: int = RERANK_TIMEOUT_MS,
) -> tuple[list[tuple[str, str, float]], bool]:
    """Rerank ``candidates`` with the cross-encoder, falling back to retrieval order on overrun.

    :param query_text: The raw question, paired with each passage.
    :param candidates: MMR's output as ``(chunk_id, chunk_text, score)``.
    :param top_k: How many candidates to return.
    :param timeout_ms: The budget. On breach the function returns the input's first ``top_k``
        **in retrieval order** with the flag set - a deliberate soft failure, because reranking
        improves an ordering that was already usable.
    :returns: ``(results, rerank_timed_out)``. On the happy path ``results`` carries the
        cross-encoder's score in the third position, replacing the incoming one: the two are on
        different scales and returning a mix would be indistinguishable from either. On the
        timeout path the incoming scores are preserved untouched, because no rerank score exists.
    """
    if not candidates:
        return [], False

    # The model is fetched BEFORE the clock starts, and the ordering is deliberate. _get_reranker
    # is lazy, so the first call in a process constructs ~1.1 GB of weights - seconds of work,
    # once. Timed inside the budget it made the very first rerank of every process breach a
    # 300 ms deadline and fall back to retrieval order, which is a cold-start artefact reported
    # as a quality event: the rerank_timeout metric would spike on every deploy and every worker
    # recycle, and an SRE alerting on it would be paged for a healthy system. Caught by
    # tests/test_rerank.py's lift test, which failed with timed_out=True on a reranker that was
    # working perfectly.
    #
    # What the budget measures is therefore the SCORING, which is the part that varies with the
    # request. The load remains a startup cost, and the honest place to pay it is a warm-up call
    # at process start - which is what the W7 D4 MCP server should do on boot.
    reranker = _get_reranker()
    started = time.perf_counter()
    # Sliced before pairing: see PASSAGE_CHAR_CAP.
    pairs = [(query_text, text[:PASSAGE_CHAR_CAP]) for _, text, _ in candidates]
    scores = reranker.predict(pairs)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    timed_out = elapsed_ms > timeout_ms
    # Counted BEFORE anything that could fail, and counted on both paths. This is the signal an
    # SRE alerts on: the soft failure changes no status code and raises nothing, so without a
    # counter a reranker that times out on every request looks identical to one that never does.
    # See taxcalc_ai.metrics for why the span attribute below is not a substitute.
    record_rerank(timed_out)
    # Attached to the ACTIVE span rather than returned only to the caller, so the breach is
    # visible in the trace even when a caller forgets to propagate the boolean. get_current_run_tree
    # returns None when tracing is off (the whole test suite), which is why it is guarded.
    run_tree = get_current_run_tree()
    if run_tree is not None:
        run_tree.extra.setdefault("metadata", {})[RERANK_TIMEOUT_ATTRIBUTE] = timed_out
        run_tree.extra["metadata"]["rerank_elapsed_ms"] = round(elapsed_ms, 2)

    if timed_out:
        _LOG.warning(
            "rerank.timeout",
            extra={
                "event": "rerank.timeout",
                "elapsed_ms": round(elapsed_ms, 2),
                "timeout_ms": timeout_ms,
                "candidates": len(candidates),
            },
        )
        # Retrieval order, unscored, flagged. Not an exception: see the module docstring.
        return list(candidates[:top_k]), True

    ranked = sorted(
        # strict=False, because `predict` returns one score per pair and the pair list was built
        # from `candidates` - the lengths cannot disagree - while strict=True would make a
        # future batching change raise inside the success path rather than at the seam.
        zip(candidates, scores, strict=False),
        key=lambda pair: float(pair[1]),
        reverse=True,
    )
    _LOG.info(
        "rerank.completed",
        extra={
            "event": "rerank.completed",
            "elapsed_ms": round(elapsed_ms, 2),
            "candidates": len(candidates),
            "returned": min(top_k, len(ranked)),
        },
    )
    return [(chunk_id, text, float(score)) for (chunk_id, text, _), score in ranked[:top_k]], False


def resolve_reranker(name: str | None = None) -> str:
    """Normalise a reranker choice to :data:`RERANKER_BGE` or :data:`RERANKER_COHERE`.

    Model ids are accepted alongside the short tokens so that an operator who sets
    ``RERANKER=rerank-3.5`` gets what they asked for rather than an error about a name that is
    obviously the thing they meant.

    **An unknown value raises, and that is the opposite of the neighbouring**
    :func:`~taxcalc_ai.rag.flag_from_env` **- deliberately.** A misspelled ``RAG_USE_RERANK``
    leaves the pipeline in its intended configuration, so defaulting is harmless there. A
    misspelled reranker name has no harmless default: silently landing on the local encoder while
    the operator believes they are paying Cohere is the exact class of bug this whole function
    exists to remove, and it is invisible in every metric either backend emits. The agent service
    validates its own value at boot (``Settings.reranker`` is a ``Literal``), so a typo there never
    reaches a request; reaching this raise means someone set the variable on a process directly.

    :param name: An explicit choice, or ``None`` to read :data:`RERANKER_ENV`.
    :returns: :data:`RERANKER_BGE` or :data:`RERANKER_COHERE`.
    :raises ValueError: on any other value.
    """
    raw = (name if name is not None else os.environ.get(RERANKER_ENV, RERANKER_BGE)).strip().lower()
    if raw in {RERANKER_BGE, RERANKER_MODEL.lower(), "bge-reranker-base"}:
        return RERANKER_BGE
    if raw in {RERANKER_COHERE, COHERE_RERANK_MODEL}:
        return RERANKER_COHERE
    raise ValueError(
        f"{RERANKER_ENV}={raw!r} is not a reranker; "
        f"expected {RERANKER_BGE!r} or {RERANKER_COHERE!r}"
    )


@traceable(run_type="chain", name="taxcalc_ai.cohere_rerank")
def cohere_rerank(
    query_text: str,
    candidates: list[tuple[str, str, float]],
    top_k: int = DEFAULT_RERANK_TOP_K,
    timeout_ms: int = RERANK_TIMEOUT_MS,
) -> tuple[list[tuple[str, str, float]], bool]:
    """Rerank ``candidates`` with Cohere ``rerank-3.5``, falling back to retrieval order on failure.

    The same soft-failure contract as :func:`bge_rerank`, the same counters, and the same return
    shape - so the two are interchangeable at the call site and the pipeline needs no branch of
    its own beyond picking one.

    **Here the timeout is real, and that is the one substantive difference from the local path.**
    ``CrossEncoder.predict`` is a blocking call into PyTorch with no cancellation seam, so
    :func:`bge_rerank` can only measure the overrun *after* it happens and report it. This is an
    HTTP request, so ``httpx``' own timeout genuinely abandons it: the 300 ms budget bounds the
    request's latency rather than merely its visibility. Choosing Cohere therefore buys a hard
    deadline as well as a better model, and pays for it in per-query cost and a network hop.

    **Every failure degrades rather than raising, including a non-timeout one.** A 429, a 503 or a
    connection reset all mean the same thing to this stage - no rerank score exists - and the
    retrieval order is a usable answer, so converting any of them into a failed request would be a
    self-inflicted outage over a quality improvement. They are all counted as
    ``rerank_timed_out``, which is the honest reading of that counter: it means "the rerank did not
    contribute", and the structured log's ``event`` field is what distinguishes a slow vendor from
    a rejected one.

    :param query_text: The raw question, scored against each passage.
    :param candidates: MMR's output as ``(chunk_id, chunk_text, score)``.
    :param top_k: How many candidates to return.
    :param timeout_ms: The budget, applied as the HTTP timeout.
    :returns: ``(results, rerank_timed_out)``, exactly as :func:`bge_rerank` returns them - Cohere's
        ``relevance_score`` in the third position on success, the incoming scores untouched on the
        fallback path.
    :raises RuntimeError: if :data:`COHERE_API_KEY_ENV` is unset. The one failure here that is NOT
        soft: a missing credential is a deployment mistake, not a latency event, and degrading past
        it would serve local-encoder quality forever while every dashboard says Cohere.
    """
    if not candidates:
        return [], False

    api_key = os.environ.get(COHERE_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"{RERANKER_ENV}={RERANKER_COHERE} requires {COHERE_API_KEY_ENV} to be set"
        )

    started = time.perf_counter()
    # Sliced before sending, for a different reason than bge's token budget: this is billed and
    # bounded request body, and a 8 KB chunk is 8 KB of upload per candidate per query.
    documents = [text[:PASSAGE_CHAR_CAP] for _, text, _ in candidates]
    failure: str | None = None
    scored: list[tuple[int, float]] = []
    try:
        response = httpx.post(
            COHERE_RERANK_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": COHERE_RERANK_MODEL,
                "query": query_text,
                "documents": documents,
                "top_n": top_k,
            },
            timeout=timeout_ms / 1000.0,
        )
        response.raise_for_status()
        body = response.json()
        scored = [
            (int(row["index"]), float(row["relevance_score"]))
            for row in body.get("results", [])
            # A response naming a document outside the list we sent is a contract violation, and
            # indexing on it would raise inside the success path. Dropped instead, which degrades
            # one result rather than the request.
            if 0 <= int(row.get("index", -1)) < len(candidates)
        ]
    except httpx.TimeoutException:
        failure = "rerank.cohere.timeout"
    except httpx.HTTPError as exc:  # transport errors and non-2xx alike - see the docstring
        failure = "rerank.cohere.error"
        _LOG.warning(
            failure,
            extra={"event": failure, "error": type(exc).__name__},
        )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    # An empty `results` on a 200 is as much a non-contribution as a timeout: returning the
    # candidates unranked-but-unflagged would claim a rerank that did not happen.
    timed_out = failure is not None or not scored
    record_rerank(timed_out)
    run_tree = get_current_run_tree()
    if run_tree is not None:
        run_tree.extra.setdefault("metadata", {})[RERANK_TIMEOUT_ATTRIBUTE] = timed_out
        run_tree.extra["metadata"]["rerank_elapsed_ms"] = round(elapsed_ms, 2)
        run_tree.extra["metadata"]["reranker"] = RERANKER_COHERE

    if timed_out:
        _LOG.warning(
            "rerank.cohere.fallback",
            extra={
                "event": "rerank.cohere.fallback",
                "cause": failure or "rerank.cohere.empty",
                "elapsed_ms": round(elapsed_ms, 2),
                "timeout_ms": timeout_ms,
                "candidates": len(candidates),
            },
        )
        return list(candidates[:top_k]), True

    # Cohere returns results already ordered by relevance, but sorting anyway rather than trusting
    # it: the ordering is a documented convenience, not a schema guarantee, and a silently
    # unsorted top-6 is indistinguishable from a working rerank at every layer above this one.
    scored.sort(key=lambda pair: pair[1], reverse=True)
    _LOG.info(
        "rerank.completed",
        extra={
            "event": "rerank.completed",
            "reranker": RERANKER_COHERE,
            "elapsed_ms": round(elapsed_ms, 2),
            "candidates": len(candidates),
            "returned": min(top_k, len(scored)),
        },
    )
    return [(candidates[i][0], candidates[i][1], score) for i, score in scored[:top_k]], False


def rerank_candidates(
    query_text: str,
    candidates: list[tuple[str, str, float]],
    top_k: int = DEFAULT_RERANK_TOP_K,
    timeout_ms: int = RERANK_TIMEOUT_MS,
    reranker: str | None = None,
) -> tuple[list[tuple[str, str, float]], bool]:
    """Rerank with whichever backend :data:`RERANKER_ENV` names.

    The pipeline's only rerank entry point, so that "which reranker" is decided in exactly one
    place. The two backends are deliberately signature-compatible: this function picks, it does not
    adapt.

    :param query_text: The raw question.
    :param candidates: MMR's output as ``(chunk_id, chunk_text, score)``.
    :param top_k: How many candidates to return.
    :param timeout_ms: The rerank budget.
    :param reranker: An explicit backend, bypassing the environment. For tests and for a caller
        that has already validated the value - which the W7 D5 agent service has, at boot.
    :returns: ``(results, rerank_timed_out)``.
    :raises ValueError: on an unrecognised backend name. See :func:`resolve_reranker`.
    """
    if resolve_reranker(reranker) == RERANKER_COHERE:
        return cohere_rerank(query_text, candidates, top_k=top_k, timeout_ms=timeout_ms)
    return bge_rerank(query_text, candidates, top_k=top_k, timeout_ms=timeout_ms)
