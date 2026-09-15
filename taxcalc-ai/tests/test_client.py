# taxcalc-ai/tests/test_client.py
"""Client tests: the retry policy, correlation-id propagation, and the secret discipline.

The proxy is mocked with ``respx`` rather than reached over the network, so the retry counts
below are exact assertions ("exactly 3 attempts", "exactly 1 attempt") rather than eventually-
consistent guesses, and the suite stays green with the W3 D1 proxy down.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

import httpx
import pytest
import respx

from taxcalc_ai.client import (
    CORRELATION_HEADER,
    JsonLogFormatter,
    LlmProxyClient,
    configure_logging,
)
from taxcalc_ai.models import LiabilityEstimateRequest
from taxcalc_ai.settings import TaxcalcAiSettings
from tests.conftest import PROXY_API_KEY, PROXY_BASE_URL

COMPLETIONS_URL = f"{PROXY_BASE_URL}/v1/completions"

COMPLETION_TEXT = json.dumps(
    {
        "label": "standard-bracket",
        "confidence": 0.93,
        "rationale": "Single filer in the mid California bracket for tax year 2026.",
        "estimatedLiability": "26400.00",
    }
)

PROXY_BODY = {
    "model": "claude-haiku-4-5",
    "resolvedModel": "claude-haiku-4-5-20251001",
    "feature": "liability-estimate",
    "inputTokens": 412,
    "outputTokens": 88,
    "text": COMPLETION_TEXT,
}


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make tenacity's backoff instantaneous.

    The wait policy is the thing under test in spirit, but sleeping through real exponential
    jitter would add seconds per retry test for no extra signal.
    """
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)


@respx.mock
def test_estimate_happy_path(
    settings: TaxcalcAiSettings, estimate_request: LiabilityEstimateRequest
) -> None:
    """A 200 yields a validated result whose identifiers come from us, not from the model."""
    route = respx.post(COMPLETIONS_URL).mock(
        return_value=httpx.Response(
            200,
            json=PROXY_BODY,
            headers={CORRELATION_HEADER: estimate_request.correlation_id, "x-cost-usd": "0.0004"},
        )
    )

    with LlmProxyClient(settings) as client:
        result = client.estimate(estimate_request)

    assert route.call_count == 1
    assert result.correlation_id == estimate_request.correlation_id
    assert result.taxpayer_id == "taxpayer-001"
    assert result.estimated_liability == Decimal("26400.00")
    # The dated snapshot the provider actually served, not the alias we asked for.
    assert result.model_id == "claude-haiku-4-5-20251001"


@respx.mock
def test_request_carries_correlation_id_and_bearer_token(
    settings: TaxcalcAiSettings, estimate_request: LiabilityEstimateRequest
) -> None:
    """The W3 D2 id goes out on the header the Java CorrelationIdFilter reads."""
    route = respx.post(COMPLETIONS_URL).mock(return_value=httpx.Response(200, json=PROXY_BODY))

    with LlmProxyClient(settings) as client:
        client.estimate(estimate_request)

    sent = route.calls[0].request
    assert sent.headers[CORRELATION_HEADER] == "corr-w7d1-0001"
    assert sent.headers["authorization"] == f"Bearer {PROXY_API_KEY}"
    body = json.loads(sent.content)
    assert body["feature"] == "liability-estimate"
    assert body["model"] == "claude-haiku-4-5"


@respx.mock
@pytest.mark.usefixtures("no_backoff_sleep")
def test_retries_exactly_three_times_on_503(
    settings: TaxcalcAiSettings, estimate_request: LiabilityEstimateRequest
) -> None:
    """A 5xx is transient: three attempts, then the last failure is re-raised."""
    route = respx.post(COMPLETIONS_URL).mock(return_value=httpx.Response(503))

    with LlmProxyClient(settings) as client, pytest.raises(httpx.HTTPStatusError):
        client.estimate(estimate_request)

    assert route.call_count == 3


@respx.mock
@pytest.mark.usefixtures("no_backoff_sleep")
def test_does_not_retry_on_400(
    settings: TaxcalcAiSettings, estimate_request: LiabilityEstimateRequest
) -> None:
    """A 4xx is the server rejecting the request itself; sending it again cannot help."""
    route = respx.post(COMPLETIONS_URL).mock(return_value=httpx.Response(400))

    with LlmProxyClient(settings) as client, pytest.raises(httpx.HTTPStatusError):
        client.estimate(estimate_request)

    assert route.call_count == 1


@respx.mock
@pytest.mark.usefixtures("no_backoff_sleep")
def test_retries_on_timeout_then_succeeds(
    settings: TaxcalcAiSettings, estimate_request: LiabilityEstimateRequest
) -> None:
    """A transport timeout is retried, and a later success is returned normally."""
    route = respx.post(COMPLETIONS_URL).mock(
        side_effect=[
            httpx.TimeoutException("connect timed out"),
            httpx.Response(200, json=PROXY_BODY),
        ]
    )

    with LlmProxyClient(settings) as client:
        result = client.estimate(estimate_request)

    assert route.call_count == 2
    assert result.label == "standard-bracket"


@respx.mock
def test_mismatched_correlation_echo_is_refused(
    settings: TaxcalcAiSettings, estimate_request: LiabilityEstimateRequest
) -> None:
    """An answer echoing somebody else's id is not attributed to this taxpayer."""
    respx.post(COMPLETIONS_URL).mock(
        return_value=httpx.Response(
            200, json=PROXY_BODY, headers={CORRELATION_HEADER: "corr-somebody-else"}
        )
    )

    with LlmProxyClient(settings) as client, pytest.raises(ValueError, match="echoed correlation"):
        client.estimate(estimate_request)


@respx.mock
def test_api_key_never_reaches_a_log_line(
    settings: TaxcalcAiSettings,
    estimate_request: LiabilityEstimateRequest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The key crosses into a plain string exactly once, on the wire - never into a log."""
    respx.post(COMPLETIONS_URL).mock(return_value=httpx.Response(200, json=PROXY_BODY))

    with (
        caplog.at_level(logging.DEBUG, logger="taxcalc_ai.client"),
        LlmProxyClient(settings) as client,
    ):
        client.estimate(estimate_request)

    rendered = "\n".join(JsonLogFormatter().format(record) for record in caplog.records)
    assert PROXY_API_KEY not in rendered
    assert "corr-w7d1-0001" in rendered
    assert "proxy.call.ok" in rendered


@respx.mock
@pytest.mark.usefixtures("no_backoff_sleep")
def test_retry_log_line_carries_correlation_and_tenant(
    settings: TaxcalcAiSettings,
    estimate_request: LiabilityEstimateRequest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The retry line is the one you most need to filter on, so it carries the ids too.

    A retry storm is exactly when a log backend has to be narrowed to a single call, and a
    ``proxy.call.retry`` line without a ``correlation_id`` cannot be narrowed at all.
    """
    respx.post(COMPLETIONS_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=PROXY_BODY)]
    )

    with (
        caplog.at_level(logging.DEBUG, logger="taxcalc_ai.client"),
        LlmProxyClient(settings) as client,
    ):
        client.estimate(estimate_request)

    retries = [
        json.loads(JsonLogFormatter().format(record))
        for record in caplog.records
        if getattr(record, "event", None) == "proxy.call.retry"
    ]

    assert len(retries) == 1
    assert retries[0]["correlation_id"] == "corr-w7d1-0001"
    assert retries[0]["tenant_id"] == "tenant-a"
    assert retries[0]["attempt"] == 1
    assert "503" in retries[0]["reason"]
    assert PROXY_API_KEY not in json.dumps(retries[0])


def test_json_log_formatter_promotes_extras_to_top_level() -> None:
    """Structured extras become real JSON properties a log backend can filter on."""
    record = logging.LogRecord(
        name="taxcalc_ai.client",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="proxy call starting",
        args=(),
        exc_info=None,
    )
    record.event = "proxy.call.start"
    record.correlation_id = "corr-1"

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["event"] == "proxy.call.start"
    assert payload["correlation_id"] == "corr-1"
    assert payload["level"] == "INFO"
    assert payload["message"] == "proxy call starting"


def test_configure_logging_maps_javas_warn_to_pythons_warning(
    settings: TaxcalcAiSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``WARN`` is Java's spelling; Python's logging module only knows ``WARNING``."""
    monkeypatch.setenv("TAXCALC_AI_LOG_LEVEL", "WARN")
    warn_settings = TaxcalcAiSettings()
    try:
        configure_logging(warn_settings)
        assert logging.getLogger().level == logging.WARNING
        configure_logging(settings)
        assert logging.getLogger().level == logging.INFO
    finally:
        logging.getLogger().handlers = []
