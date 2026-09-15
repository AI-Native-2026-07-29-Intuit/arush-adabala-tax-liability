# taxcalc-ai/src/taxcalc_ai/client.py
"""httpx client for the W3 D1 LLM proxy, with timeouts, retries and structured logging.

Three disciplines are wired together here, and each one exists because of a specific failure:

**Timeouts.** ``httpx`` defaults to a 5 second timeout, but a client constructed without an
explicit one is a client whose behaviour changes when the library's default changes. The
timeout comes from :class:`~taxcalc_ai.settings.TaxcalcAiSettings` and is passed explicitly.

**Retries, but only on transient failures.** A timeout, a connection reset or a 5xx is worth
trying again; a 4xx is not. Retrying a 400 just spends the rate-limit budget three times to get
the same rejection, and retrying a 401 can lock an account out. :func:`_is_transient` is the
predicate that draws that line, and it is why this module does not use a bare
``retry_if_exception_type(httpx.HTTPStatusError)`` - that would retry 4xx too.

**Structured logs that carry the correlation id and never the API key.** Every line is JSON
with an ``event`` name, the ``correlation_id`` and the ``tenant_id`` - the retry line included,
which is the one that matters most, because a retry storm is exactly when you want to filter a
log backend down to a single call. A support ticket quoting one id can therefore be traced
across the Java service and this sidecar.
``SecretStr.get_secret_value()`` is called in exactly one place in this package - the line that
builds the ``authorization`` header - so there is one place to audit.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import Final

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .models import (
    EstimateCompletion,
    LiabilityEstimateRequest,
    LiabilityEstimateResult,
    ProxyCompletionRequest,
    ProxyCompletionResponse,
    Taxpayer,
)
from .settings import TaxcalcAiSettings
from .value_types import CorrelationContext, ProxyCallKey

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.client")

#: The proxy route. Served by the Java service's `LlmProxyController` (W6 D4).
COMPLETIONS_PATH: Final[str] = "/v1/completions"

#: The W3 D2 correlation-id header. `CorrelationIdFilter` on the Java side reads it and echoes
#: it back on the response, which is what makes the echo assertion below possible.
CORRELATION_HEADER: Final[str] = "x-correlation-id"

#: Set by the Java `CostResponseHeader`; carries what this one call cost, in USD.
COST_HEADER: Final[str] = "x-cost-usd"

_HTTP_SERVER_ERROR: Final[int] = 500

#: Attributes `logging.LogRecord` sets itself. Anything outside this set came from `extra=`
#: and is therefore a structured field this formatter should emit.
_STANDARD_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonLogFormatter(logging.Formatter):
    """Render each log record as a single JSON object.

    One line, one object, with every ``extra=`` key promoted to a top-level property. That
    shape is what lets a log backend filter on ``correlation_id`` directly, rather than
    regex-ing it out of a human-readable message.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialise ``record`` to a JSON line, merging in its structured extras."""
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(settings: TaxcalcAiSettings) -> None:
    """Install :class:`JsonLogFormatter` on the root logger at the configured level.

    Kept out of import time and out of the client's constructor: a library that reconfigures
    logging when it is imported, or once per client it constructs, fights whatever the hosting
    application already set up. The CLI calls this once; a host application need not call it
    at all.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    # WARN is Java's spelling; Python's logging module only knows WARNING.
    root.setLevel("WARNING" if settings.log_level == "WARN" else settings.log_level)
    root.handlers = [handler]
    # httpx logs one INFO line per request with no `event` and no correlation id, which is the
    # only thing that would break the "every line carries the ids" property of this stream -
    # and it says nothing `proxy.call.start` and `proxy.call.ok` do not already say with them.
    # Raised to WARNING rather than silenced, so its transport warnings still surface.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _log_retry(state: RetryCallState) -> None:
    """Log one structured line per retry, naming the attempt, the reason and the caller.

    Reads the :class:`~taxcalc_ai.value_types.CorrelationContext` back off the call state so
    this line carries the same ``correlation_id`` and ``tenant_id`` as every other line. That
    is why :meth:`LlmProxyClient._post` takes its arguments keyword-only: ``state.kwargs`` is
    then a stable place to find the context, where ``state.args`` positions would shift the
    moment the signature changed.
    """
    outcome = state.outcome
    reason = repr(outcome.exception()) if outcome is not None and outcome.failed else None
    fields: dict[str, object] = {
        "event": "proxy.call.retry",
        "attempt": state.attempt_number,
        "reason": reason,
    }
    context = state.kwargs.get("context")
    if isinstance(context, CorrelationContext):
        fields.update(context.as_log_fields())
    _LOG.warning("retrying proxy call", extra=fields)


def _is_transient(exc: BaseException) -> bool:
    """Decide whether ``exc`` is worth another attempt.

    Timeouts and network errors are transient by definition. An HTTP status error is transient
    only at 5xx: a 4xx is the server saying the request itself is wrong, and sending it again
    unchanged cannot make it right.
    """
    if isinstance(exc, httpx.TimeoutException | httpx.NetworkError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= _HTTP_SERVER_ERROR
    return False


class LlmProxyClient:
    """Synchronous client for the LLM proxy. An ``async`` variant lands on W7 D2.

    Usable as a context manager, which is the recommended form: the underlying
    :class:`httpx.Client` owns a connection pool, and a client that is never closed leaks
    sockets for the life of the process.
    """

    def __init__(self, settings: TaxcalcAiSettings) -> None:
        """Build the pooled HTTP client from ``settings``."""
        self._settings = settings
        self._client = httpx.Client(
            base_url=str(settings.proxy_base_url),
            timeout=httpx.Timeout(settings.proxy_timeout_seconds),
            headers={"user-agent": "taxcalc-ai/0.1.0"},
        )

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> LlmProxyClient:
        """Return self; the pool is already open."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the pool on the way out, successful or not."""
        self.close()

    def estimate(self, request: LiabilityEstimateRequest) -> LiabilityEstimateResult:
        """Run one liability estimation round-trip against the proxy.

        The transport call is retried per the policy above; everything around it - prompt
        construction, response validation, envelope assembly - happens exactly once, because
        none of it can fail transiently.

        :param request: the validated request envelope.
        :return: the validated result envelope, carrying the request's own correlation id.
        :raises httpx.HTTPStatusError: on a non-retryable status, or after the last retry.
        :raises pydantic.ValidationError: if the proxy or the model returned a payload that
            does not satisfy the boundary contract.
        """
        context = CorrelationContext(
            correlation_id=request.correlation_id,
            tenant_id=self._settings.tenant_id,
            started_at=datetime.now(tz=UTC),
            tags=(request.feature,),
        )
        prompt = _build_prompt(request.taxpayer)
        key = ProxyCallKey(
            correlation_id=request.correlation_id,
            model_id=request.model_id,
            prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:16],
        )
        wire = ProxyCompletionRequest(
            prompt=prompt, model=request.model_id, feature=request.feature
        )

        _LOG.info(
            "proxy call starting",
            extra={
                "event": "proxy.call.start",
                "model_id": key.model_id,
                "prompt_hash": key.prompt_hash,
                **context.as_log_fields(),
            },
        )

        # `_post` carries the retry policy as its decorator. `retry_with` copies that policy
        # with a single field overridden, so proxy_max_retries stays a live environment knob
        # instead of a setting nothing reads; at its default of 3 the copy is identical to the
        # declared policy. The copy wraps the undecorated function, so it is unbound - hence
        # the explicit `self`.
        attempt = LlmProxyClient._post.retry_with(  # type: ignore[attr-defined]
            stop=stop_after_attempt(self._settings.proxy_max_retries)
        )
        response = attempt(self, wire=wire, context=context)
        completion = self._read_completion(response, context)
        estimate = EstimateCompletion.model_validate_json(completion.text)

        _LOG.info(
            "proxy call succeeded",
            extra={
                "event": "proxy.call.ok",
                "resolved_model": completion.resolved_model,
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                # Present only when the Java CostResponseHeader set it; absent in unit tests.
                "cost_usd": response.headers.get(COST_HEADER),
                "label": estimate.label,
                "confidence": estimate.confidence,
                **context.as_log_fields(),
            },
        )

        return LiabilityEstimateResult(
            correlation_id=request.correlation_id,
            taxpayer_id=request.taxpayer.id,
            label=estimate.label,
            confidence=estimate.confidence,
            rationale=estimate.rationale,
            estimated_liability=estimate.estimated_liability,
            model_id=completion.resolved_model,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=8.0),
        retry=retry_if_exception(_is_transient),
        before_sleep=_log_retry,
        reraise=True,
    )
    def _post(self, *, wire: ProxyCompletionRequest, context: CorrelationContext) -> httpx.Response:
        """Perform one attempt: POST the completion request and raise on any error status.

        Kept to exactly the work that can fail transiently, because this is the unit the retry
        policy above repeats. Arguments are keyword-only so that :func:`_log_retry` can find
        the context by name on the call state.

        ``reraise=True`` means the caller sees the underlying ``HTTPStatusError`` after the
        last attempt, not a ``RetryError`` wrapping it.
        """
        payload: dict[str, object] = wire.model_dump(mode="json", by_alias=True)
        response = self._client.post(
            COMPLETIONS_PATH,
            json=payload,
            headers={
                CORRELATION_HEADER: context.correlation_id,
                # The one place in this package where the secret becomes a plain string.
                "authorization": f"Bearer {self._settings.proxy_api_key.get_secret_value()}",
            },
        )
        # Raises for 4xx and 5xx alike; _is_transient is what decides which of those come back.
        response.raise_for_status()
        return response

    def _read_completion(
        self, response: httpx.Response, context: CorrelationContext
    ) -> ProxyCompletionResponse:
        """Validate the proxy's body, after checking it answered the question we asked.

        The Java ``CorrelationIdFilter`` echoes ``X-Correlation-Id`` on every response, so a
        mismatch means the response in hand belongs to a different request - a proxy bug, or a
        cache serving a crossed pair. Either way the safe move is to refuse it rather than
        attribute somebody else's answer to this taxpayer.
        """
        echoed = response.headers.get(CORRELATION_HEADER)
        if echoed is not None and echoed != context.correlation_id:
            _LOG.error(
                "proxy echoed a different correlation id",
                extra={
                    "event": "proxy.call.correlation_mismatch",
                    "echoed_correlation_id": echoed,
                    **context.as_log_fields(),
                },
            )
            raise ValueError(
                f"proxy echoed correlation id {echoed!r}, expected {context.correlation_id!r}"
            )
        # model_validate_json reads the bytes directly through pydantic-core, which is faster
        # than json.loads followed by model_validate and gives better error locations.
        return ProxyCompletionResponse.model_validate_json(response.content)


def _build_prompt(taxpayer: Taxpayer) -> str:
    """Render the taxpayer into the prompt sent upstream.

    The model is asked for a bare JSON object matching
    :class:`~taxcalc_ai.models.EstimateCompletion`; the schema is spelled out in the prompt so
    that the ``extra="forbid"`` validation on the way back has a fair chance of passing. The
    taxpayer's own record is serialised by alias, so the model sees the same camelCase field
    names the Java service uses.
    """
    record = taxpayer.model_dump_json(by_alias=True)
    return (
        "You are a tax liability estimator. Given the taxpayer record below, respond with a "
        "single JSON object and nothing else, with exactly these keys: "
        '"label" (short category string), "confidence" (number between 0 and 1), '
        '"rationale" (one or two sentences), "estimatedLiability" (decimal string, 2 places). '
        f"Taxpayer record: {record}"
    )
