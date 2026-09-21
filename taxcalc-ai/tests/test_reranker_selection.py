# taxcalc-ai/tests/test_reranker_selection.py
"""``RERANKER`` actually picks a backend, and the Cohere backend actually reranks.

This file exists because of a specific bug rather than a general worry. The W7 D5 agent service
exported a reranker choice into the environment under a name nothing read, and the pipeline
hard-coded the local cross-encoder - so setting the variable to Cohere changed the environment,
changed no behaviour, and reported no error. A dead knob is worse than a missing one: it looks
configured.

So the assertions here are deliberately about the *wiring* rather than about rerank quality (the
lift claim lives in tests/test_rerank.py, against the real encoder). What is gated:

* the name of the environment variable, and that the agent service sets the same one;
* that each accepted value routes to a different function;
* that an unrecognised value raises instead of quietly landing on the local encoder;
* that the Cohere path reorders by the vendor's score, fails soft on every transport failure, and
  raises - loudly, once, at the top - when its credential is missing.

No model load and no network: the local backend is substituted by name and the Cohere endpoint is
served by ``respx``. That keeps the whole file in milliseconds, which matters because the thing
being tested is a dispatch decision, not inference.
"""

from __future__ import annotations

import pathlib

import httpx
import pytest
import respx

import taxcalc_ai
from taxcalc_ai.metrics import RERANK_REQUESTS_COUNTER, RERANK_TIMEOUT_COUNTER
from taxcalc_ai.rerank import (
    COHERE_API_KEY_ENV,
    COHERE_RERANK_MODEL,
    COHERE_RERANK_URL,
    RERANKER_BGE,
    RERANKER_COHERE,
    RERANKER_ENV,
    cohere_rerank,
    rerank_candidates,
    resolve_reranker,
)

#: Three candidates in deliberately wrong order, so "did the rerank happen" is answerable from the
#: output alone: index 2 is the relevant one and arrives last.
CANDIDATES: list[tuple[str, str, float]] = [
    ("chunk-a-p0", "unrelated boilerplate about office furniture", 0.9),
    ("chunk-b-p0", "more unrelated text about parking", 0.8),
    ("chunk-c-p0", "the home office deduction is limited to the square footage used", 0.1),
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no reranker choice and no credential set.

    Autouse because both variables are read from the process environment, and a developer with
    ``COHERE_API_KEY`` exported in their shell would otherwise get different results from CI on the
    two tests that assert on its absence.
    """
    monkeypatch.delenv(RERANKER_ENV, raising=False)
    monkeypatch.delenv(COHERE_API_KEY_ENV, raising=False)


@pytest.fixture
def cohere_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake credential, for the tests that exercise the request rather than its absence.

    Requested explicitly rather than folded into the autouse fixture above, so the two tests that
    assert on a MISSING key cannot be made to pass by a fixture quietly supplying one.
    """
    monkeypatch.setenv(COHERE_API_KEY_ENV, "co-test-key")


def _counts() -> tuple[float, float]:
    """Current ``(requests, timeouts)`` totals.

    Deltas rather than absolutes, for the reason tests/test_metrics.py gives: the counters are
    process-global and another test may have reranked first.
    """
    return (RERANK_REQUESTS_COUNTER._value.get(), RERANK_TIMEOUT_COUNTER._value.get())


# ------------------------------------------------------------------ the selector


def test_an_unset_variable_selects_the_local_encoder() -> None:
    """The default is the backend that needs no credential and no network."""
    assert resolve_reranker(None) == RERANKER_BGE


@pytest.mark.parametrize(
    "value", ["bge", "BGE", " bge ", "bge-reranker-base", "BAAI/bge-reranker-base"]
)
def test_every_spelling_of_the_local_encoder_resolves_to_it(value: str) -> None:
    """Model ids are accepted alongside the short token.

    An operator who sets ``RERANKER=BAAI/bge-reranker-base`` has said something unambiguous, and
    rejecting it to insist on ``bge`` would be pedantry with a production outage attached.
    """
    assert resolve_reranker(value) == RERANKER_BGE


@pytest.mark.parametrize("value", ["cohere", "Cohere", "rerank-3.5"])
def test_every_spelling_of_cohere_resolves_to_it(value: str) -> None:
    """Including the pinned model id, for the same reason as the local aliases."""
    assert resolve_reranker(value) == RERANKER_COHERE


def test_an_unrecognised_name_raises_rather_than_defaulting() -> None:
    """The assertion that makes this a knob and not a suggestion.

    Defaulting a typo to the local encoder is the failure mode this whole module replaced: the
    operator believes they are paying for Cohere, every metric either backend emits looks healthy,
    and nothing anywhere says otherwise. Deliberately the OPPOSITE of ``flag_from_env``, whose
    misspelled stage flags default to on - there, the default is the intended configuration; here,
    there is no harmless guess.
    """
    with pytest.raises(ValueError, match="is not a reranker"):
        resolve_reranker("cohore")


def test_the_environment_is_read_when_no_explicit_choice_is_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RERANKER``, unprefixed - the literal name the agent service exports."""
    monkeypatch.setenv(RERANKER_ENV, "cohere")
    assert resolve_reranker() == RERANKER_COHERE


def test_an_explicit_argument_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """So a caller that has already validated its own value need not mutate the environment."""
    monkeypatch.setenv(RERANKER_ENV, "cohere")
    assert resolve_reranker(RERANKER_BGE) == RERANKER_BGE


# ------------------------------------------------------------------ dispatch


def test_the_variable_decides_which_backend_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both branches of ``rerank_candidates``, asserted by which function was called.

    Substituting both backends rather than letting either run: this asserts the dispatch, and a
    real 1.1 GB encoder on one side and a real HTTP call on the other would make the test about
    everything except the dispatch.
    """
    called: list[str] = []

    def fake_bge(*_args: object, **_kwargs: object) -> tuple[list[tuple[str, str, float]], bool]:
        """Record that the local backend ran."""
        called.append(RERANKER_BGE)
        return [], False

    def fake_cohere(*_args: object, **_kwargs: object) -> tuple[list[tuple[str, str, float]], bool]:
        """Record that the hosted backend ran."""
        called.append(RERANKER_COHERE)
        return [], False

    monkeypatch.setattr("taxcalc_ai.rerank.bge_rerank", fake_bge)
    monkeypatch.setattr("taxcalc_ai.rerank.cohere_rerank", fake_cohere)

    rerank_candidates("q", CANDIDATES)
    monkeypatch.setenv(RERANKER_ENV, "cohere")
    rerank_candidates("q", CANDIDATES)

    assert called == [RERANKER_BGE, RERANKER_COHERE]


def test_the_pipeline_reranks_through_the_selector() -> None:
    """``retrieve_and_generate`` must call the dispatcher, not one backend directly.

    Read off the source rather than by importing :mod:`taxcalc_ai.rag`, and the reason is measured:
    that module builds a :class:`~sentence_transformers.SentenceTransformer` at import, so an
    ``is``-identity assertion against the imported symbol cost 148 seconds - more than the rest of
    this file and most of the suite combined - to check one name. The pipeline itself is exercised
    against real clients in tests/test_rag_pipeline.py; what is gated here is only that a future
    edit cannot "simplify" the call site back to ``bge_rerank`` and silently restore the dead knob.
    """
    source = (pathlib.Path(taxcalc_ai.__file__).parent / "rag.py").read_text()

    assert "rerank_candidates(" in source
    # The import line is allowed to name it; a CALL is not.
    assert "bge_rerank(" not in source


# ------------------------------------------------------------------ the Cohere backend


def test_cohere_reorders_by_the_vendors_relevance_score(cohere_key: None) -> None:
    """The buried relevant chunk comes back first, carrying Cohere's score and not the incoming one.

    The third tuple position is asserted explicitly: returning the retrieval score alongside a
    reranked ORDER would be indistinguishable from a working rerank at every layer above this, and
    is exactly what the fallback path returns.
    """
    with respx.mock:
        route = respx.post(COHERE_RERANK_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 2, "relevance_score": 0.97},
                        {"index": 0, "relevance_score": 0.11},
                    ]
                },
            )
        )
        results, timed_out = cohere_rerank("home office deduction", CANDIDATES, top_k=2)

    assert timed_out is False
    assert [chunk_id for chunk_id, _, _ in results] == ["chunk-c-p0", "chunk-a-p0"]
    assert results[0][2] == pytest.approx(0.97)
    body = route.calls[0].request
    assert b'"model":"rerank-3.5"' in body.content.replace(b" ", b"")
    assert COHERE_RERANK_MODEL == "rerank-3.5"


def test_cohere_sorts_the_response_rather_than_trusting_its_order(cohere_key: None) -> None:
    """A response returned worst-first still comes back best-first.

    Cohere documents its results as ordered; that is a convenience, not a schema guarantee, and a
    silently unsorted top-k is invisible above this function.
    """
    with respx.mock:
        respx.post(COHERE_RERANK_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 0, "relevance_score": 0.10},
                        {"index": 2, "relevance_score": 0.99},
                    ]
                },
            )
        )
        results, _ = cohere_rerank("q", CANDIDATES, top_k=2)

    assert [chunk_id for chunk_id, _, _ in results] == ["chunk-c-p0", "chunk-a-p0"]


def test_an_index_outside_the_sent_documents_is_dropped_not_indexed(cohere_key: None) -> None:
    """A contract violation degrades one result instead of raising inside the success path."""
    with respx.mock:
        respx.post(COHERE_RERANK_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 99, "relevance_score": 0.99},
                        {"index": 1, "relevance_score": 0.50},
                    ]
                },
            )
        )
        results, timed_out = cohere_rerank("q", CANDIDATES, top_k=3)

    assert timed_out is False
    assert [chunk_id for chunk_id, _, _ in results] == ["chunk-b-p0"]


def test_a_timeout_falls_back_to_retrieval_order_with_the_flag_set(cohere_key: None) -> None:
    """Soft failure, same contract as the local backend: the incoming order, scores untouched.

    A rerank is a quality improvement on an ordering that is already usable, so a slow vendor must
    not become a failed request. The flag is what makes the degradation visible to the caller.
    """
    requests_before, timeouts_before = _counts()
    with respx.mock:
        respx.post(COHERE_RERANK_URL).mock(side_effect=httpx.ReadTimeout("too slow"))
        results, timed_out = cohere_rerank("q", CANDIDATES, top_k=2, timeout_ms=5)

    assert timed_out is True
    assert results == CANDIDATES[:2]
    requests_after, timeouts_after = _counts()
    assert requests_after - requests_before == 1
    assert timeouts_after - timeouts_before == 1


def test_a_rejected_request_degrades_too_rather_than_raising(cohere_key: None) -> None:
    """A 429 and a timeout mean the same thing to this stage: no rerank score exists.

    Counted as a timeout on purpose - ``rerank_timed_out`` reads as "the rerank did not
    contribute", and splitting the counter by cause would mean an SRE's alert expression has to
    know the cause list. The structured log carries which one it was.
    """
    with respx.mock:
        respx.post(COHERE_RERANK_URL).mock(return_value=httpx.Response(429, json={}))
        results, timed_out = cohere_rerank("q", CANDIDATES, top_k=3)

    assert timed_out is True
    assert results == CANDIDATES


def test_an_empty_results_list_on_a_200_counts_as_no_rerank(cohere_key: None) -> None:
    """Otherwise the candidates come back unranked but unflagged - a rerank that never happened."""
    with respx.mock:
        respx.post(COHERE_RERANK_URL).mock(return_value=httpx.Response(200, json={"results": []}))
        results, timed_out = cohere_rerank("q", CANDIDATES, top_k=3)

    assert timed_out is True
    assert results == CANDIDATES


def test_a_missing_credential_raises_instead_of_degrading() -> None:
    """The one failure here that is NOT soft, and the asymmetry is the point.

    Degrading past a missing key would serve local-encoder quality forever while every dashboard
    and every log line says Cohere. That is a deployment mistake, not a latency event, so it is
    loud - and the agent service's ``Settings`` refuses to boot in this state precisely so that a
    request never reaches this raise.
    """
    with pytest.raises(RuntimeError, match=COHERE_API_KEY_ENV):
        cohere_rerank("q", CANDIDATES)


def test_no_candidates_is_not_an_error_and_makes_no_request() -> None:
    """Same early return as the local backend, and no credential needed to take it."""
    with respx.mock:
        route = respx.post(COHERE_RERANK_URL)
        assert cohere_rerank("q", []) == ([], False)
    assert route.call_count == 0
