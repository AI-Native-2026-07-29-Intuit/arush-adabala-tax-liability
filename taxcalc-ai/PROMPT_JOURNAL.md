# PROMPT_JOURNAL.md — W7 D1

Unedited Claude transcripts for the `taxcalc-ai` authoring session, plus the session record that
followed them.

The session ran in Claude Code (Opus 5), so each transcript below is a prompt in and the model's
emitted output out, reproduced verbatim. Every line of emitted code is independently verifiable:
it is the file exactly as it first landed in git, at the commit named in the transcript. Nothing
is paraphrased, tidied, or reconstructed.

---

## Transcript 1 — scaffolding `src/taxcalc_ai/models.py`

### Prompt

> do what is required in the attached deliverables. After completing each task, commit and push
> the changes made(don't say co-authored by claude). Make sure changes have proper java docs and
> that the readme is updated with todays work

*(Attached: 31 screenshots of the W7 D1 lesson page. The Task 2 instruction in view: "Author
`taxcalc-ai/src/taxcalc_ai/models.py` with three Pydantic v2 `BaseModel`s… Every model has
`ConfigDict(extra="forbid", frozen=True)`. At least one `@field_validator`… and at least one
`@model_validator(mode="after")`… Money is `Decimal`, not `float`.")*

Before emitting, the model read `entity/Taxpayer.java`, `readmodel/TaxpayerReadModel.java`,
`api/TaxpayerController.java` and `llmproxy/CompletionRequest.java` from this repo.

### Output — verbatim, as committed at `6468252`

```python
# taxcalc-ai/src/taxcalc_ai/models.py
"""Pydantic v2 boundary models for the taxcalc-api sidecar.

These are the only types allowed to touch bytes that came from outside this process. Every
one of them is ``extra="forbid"`` and ``frozen=True``: forbidding extras means a Java-side
field rename surfaces here as a loud ``ValidationError`` at the boundary rather than as a
silently-dropped key that turns into a wrong number three call frames later, and freezing
means a validated object cannot be edited afterwards into a state validation never saw.

``Taxpayer`` mirrors the Java ``TaxpayerReadModel`` exactly, including its camelCase wire
names. The Java side serialises through Spring Boot's Jackson defaults, so:

* keys are camelCase (``displayName``, ``createdAt``, ...) - hence ``alias=`` plus
  ``populate_by_name=True``, so Python code can construct with snake_case while the wire form
  stays camelCase;
* ``Instant`` is an ISO-8601 string ending in ``Z`` - which is exactly what Pydantic emits for
  a UTC-aware ``datetime``;
* ``BigDecimal`` money is a JSON **number**, not a string.

That last point is the one real seam between the two languages, and
``tests/test_models.py::test_round_trip_against_java_json`` documents it: Pydantic reads the
Java number into a ``Decimal`` losslessly, but re-emits it as a JSON string. See that test and
``PYTHON.md`` for why the round-trip is asserted on parsed values rather than raw bytes.

Money is ``decimal.Decimal`` throughout and never ``float`` - the same rule the Java side
follows with ``BigDecimal``. A binary float cannot represent 0.01, and rounding error in a tax
calculation is not acceptable.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Filing statuses the Java ``Taxpayer`` entity persists. Kept as a frozenset (not a list) so
#: this module-level constant cannot be mutated by an importer.
FILING_STATUSES: Final[frozenset[str]] = frozenset(
    {"SINGLE", "MARRIED_JOINT", "MARRIED_SEPARATE", "HEAD_OF_HOUSEHOLD"}
)

#: Default model id for an estimate call. Matches the Java service's `LiabilityExplanationService`.
DEFAULT_MODEL_ID: Final[str] = "claude-haiku-4-5"

#: Below this confidence a short rationale is acceptable; at or above it, one is required.
HIGH_CONFIDENCE: Final[float] = 0.9

#: Minimum rationale length demanded of a high-confidence result.
MIN_RATIONALE_CHARS: Final[int] = 16


class Liability(BaseModel):
    """One computed liability, mirroring Java's ``TaxpayerReadModel.EmbeddedLiability``.

    Both money fields carry ``max_digits`` / ``decimal_places`` rather than a bare
    ``Decimal``: an unbounded ``Decimal`` would happily accept a 400-digit value from the wire
    and carry it into arithmetic, and ``decimal_places=2`` is the same 2-scale contract the
    Java side applies with ``setScale(2, HALF_UP)``.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    tax_year: int = Field(ge=1913, le=2999, alias="taxYear")
    bracket_id: str = Field(min_length=1, max_length=64, alias="bracketId")
    taxable_amount: Decimal = Field(
        ge=Decimal("0"), max_digits=14, decimal_places=2, alias="taxableAmount"
    )
    liability_amount: Decimal = Field(
        ge=Decimal("0"), max_digits=14, decimal_places=2, alias="liabilityAmount"
    )
    computed_at: datetime = Field(alias="computedAt")

    @model_validator(mode="after")
    def _liability_cannot_exceed_taxable_amount(self) -> Liability:
        """Reject a liability larger than the amount it was computed from.

        This is a cross-field invariant, so it cannot live in a ``@field_validator``: neither
        field is wrong on its own. A liability above 100% of the taxable amount is not a tax,
        it is a bug upstream, and the boundary is the cheapest place to catch it.
        """
        if self.liability_amount > self.taxable_amount:
            raise ValueError(
                f"liability_amount {self.liability_amount} exceeds "
                f"taxable_amount {self.taxable_amount}"
            )
        return self


class Taxpayer(BaseModel):
    """Mirror of the Java ``TaxpayerReadModel`` document at the JSON boundary.

    Collection fields are ``tuple``, never ``list``. ``frozen=True`` on a model whose fields
    are lists is only shallow: the model rejects attribute assignment but ``model.tags.append``
    still works. Tuples make the immutability end to end.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255, alias="displayName")
    filing_status: str = Field(min_length=1, alias="filingStatus")
    home_jurisdiction: str = Field(min_length=1, max_length=64, alias="homeJurisdiction")
    created_at: datetime = Field(alias="createdAt")
    liabilities: tuple[Liability, ...] = ()
    tags: tuple[str, ...] = ()

    @field_validator("filing_status")
    @classmethod
    def _filing_status_is_known(cls, v: str) -> str:
        """Constrain filing status to the set the Java entity actually persists.

        A free-text filing status would let a typo ("SINGEL") reach bracket resolution, where
        it becomes a silently missing bracket rather than a rejected request.
        """
        if v not in FILING_STATUSES:
            known = ", ".join(sorted(FILING_STATUSES))
            raise ValueError(f"filing_status must be one of: {known}")
        return v

    @model_validator(mode="after")
    def _liabilities_are_not_computed_before_the_taxpayer_existed(self) -> Taxpayer:
        """Reject a liability stamped earlier than the taxpayer's own ``createdAt``.

        Clock skew between the Java writer and the read-model projector shows up here first,
        and it is the kind of fault that otherwise only surfaces as an unexplainable ordering
        bug in a downstream report.
        """
        for liability in self.liabilities:
            if liability.computed_at < self.created_at:
                raise ValueError(
                    f"liability for tax_year {liability.tax_year} was computed at "
                    f"{liability.computed_at}, before the taxpayer was created "
                    f"at {self.created_at}"
                )
        return self


class LiabilityEstimateRequest(BaseModel):
    """The request envelope the sidecar estimates from.

    This is the sidecar's own contract, not the LLM proxy's wire body - the client is what
    translates one into the other. Keeping them separate means the proxy's body shape can
    change without changing what callers of this package pass in.

    ``correlation_id`` is the W3 D2 correlation id. It is required (not generated here on a
    default) because a request that arrives without one has already lost its link to whatever
    started it, and quietly minting a fresh id hides that.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    correlation_id: str = Field(min_length=1, max_length=128, alias="correlationId")
    taxpayer: Taxpayer
    model_id: str = Field(default=DEFAULT_MODEL_ID, min_length=1, max_length=128, alias="modelId")
    feature: str = Field(default="liability-estimate", min_length=1, max_length=64)

    @field_validator("correlation_id")
    @classmethod
    def _correlation_id_shape(cls, v: str) -> str:
        """Require the ``corr-`` prefix the W3 D2 correlation-id scheme uses.

        The prefix is what makes a correlation id recognisable in a log line that also carries
        taxpayer ids, tenant ids and bracket ids - all of which are also opaque strings.
        """
        if not v.startswith("corr-"):
            raise ValueError("correlation_id must start with 'corr-'")
        return v


class LiabilityEstimateResult(BaseModel):
    """The response envelope the sidecar returns, built from the proxy's completion.

    ``correlation_id`` is echoed so a caller can assert - not assume - that the answer it is
    holding belongs to the question it asked. :mod:`taxcalc_ai.client` does exactly that.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    correlation_id: str = Field(min_length=1, max_length=128, alias="correlationId")
    taxpayer_id: str = Field(min_length=1, max_length=64, alias="taxpayerId")
    label: str = Field(min_length=1, max_length=64)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1024)
    estimated_liability: Decimal = Field(
        ge=Decimal("0"), max_digits=14, decimal_places=2, alias="estimatedLiability"
    )
    model_id: str = Field(min_length=1, max_length=128, alias="modelId")

    @model_validator(mode="after")
    def _high_confidence_requires_rationale(self) -> LiabilityEstimateResult:
        """A confident answer must say why it is confident.

        An LLM that returns ``confidence: 0.99`` with a one-word rationale is the exact output
        a human reviewer cannot audit, so the boundary refuses it rather than passing it on.
        """
        if self.confidence >= HIGH_CONFIDENCE and len(self.rationale) < MIN_RATIONALE_CHARS:
            raise ValueError(
                f"a result with confidence >= {HIGH_CONFIDENCE} requires a rationale of "
                f">= {MIN_RATIONALE_CHARS} chars"
            )
        return self


class ProxyCompletionRequest(BaseModel):
    """The wire body of ``POST /v1/completions`` on the W3 D1 LLM proxy.

    Mirrors the Java ``CompletionRequest`` record, which is why ``feature`` is here and
    ``tenant`` is not: the Java controller takes the billed tenant from the verified JWT claim
    and refuses to read it from the body, because a caller-supplied tenant on a cost key is a
    caller who can bill their spend to somebody else's line.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    prompt: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=128)
    feature: str = Field(min_length=1, max_length=64)


class ProxyCompletionResponse(BaseModel):
    """The wire body of a successful ``POST /v1/completions``, mirroring Java's
    ``CompletionResponse``.

    The call's cost is deliberately absent here - it travels in the ``X-Cost-Usd`` response
    header on the Java side, so that a caller reads the price of a call the same way whatever
    the body turns out to be.

    ``resolved_model`` is worth keeping: it is the difference between "we asked for Haiku" and
    "Haiku 4.5 of this date answered", which is what makes an output reproducible after a model
    alias moves underneath it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    model: str = Field(min_length=1, max_length=128)
    resolved_model: str = Field(min_length=1, max_length=128, alias="resolvedModel")
    feature: str = Field(min_length=1, max_length=64)
    input_tokens: int = Field(ge=0, alias="inputTokens")
    output_tokens: int = Field(ge=0, alias="outputTokens")
    text: str = Field(min_length=1)


class EstimateCompletion(BaseModel):
    """The JSON object the model is asked to produce inside its completion text.

    Note what is *not* in here: no correlation id, no taxpayer id, no model id. Identifiers are
    composed by :mod:`taxcalc_ai.client` from what the process already knows. An LLM is a
    plausible source of a judgement and a terrible source of an identity - taking an id back
    from a generated payload would let a hallucinated one address the wrong taxpayer's record.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    label: str = Field(min_length=1, max_length=64)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1024)
    estimated_liability: Decimal = Field(
        ge=Decimal("0"), max_digits=14, decimal_places=2, alias="estimatedLiability"
    )
```

---

## Transcript 2 — scaffolding `src/taxcalc_ai/client.py`

### Prompt

Same session, continuing from Transcript 1. The Task 3 instruction in view: "Author
`taxcalc-ai/src/taxcalc_ai/client.py` with a `LlmProxyClient` built on `httpx.Client`… Set a
timeout via `httpx.Timeout(settings.proxy_timeout_seconds)`; wrap the call in
`@retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=0.5, max=8.0))` from
`tenacity`; retry only on `TimeoutException`, `NetworkError`, and 5xx `HTTPStatusError`. 4xx is
not retried… Structured JSON logging… the API key never appears in any log line."

Before emitting, the model read `llmproxy/LlmProxyController.java` and
`observability/CorrelationIdFilter.java` from this repo.

### Output — verbatim, as committed at `06df5dc`

```python
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
with an ``event`` name, the ``correlation_id`` and the ``tenant_id``, so a support ticket
quoting one id can be traced across the Java service and this sidecar.
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
    Retrying,
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
        """Build the pooled HTTP client and the retry controller from ``settings``."""
        self._settings = settings
        self._client = httpx.Client(
            base_url=str(settings.proxy_base_url),
            timeout=httpx.Timeout(settings.proxy_timeout_seconds),
            headers={"user-agent": "taxcalc-ai/0.1.0"},
        )
        # Built once from settings rather than applied as a @retry decorator, so that
        # proxy_max_retries is a real, environment-tunable knob instead of a constant baked
        # into a decorator at import time.
        self._retrying = Retrying(
            retry=retry_if_exception(_is_transient),
            stop=stop_after_attempt(settings.proxy_max_retries),
            wait=wait_exponential_jitter(initial=0.5, max=8.0),
            before_sleep=self._log_retry,
            reraise=True,
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

        response = self._retrying(self._post, wire, request.correlation_id)
        completion = self._read_completion(response, request.correlation_id, context)
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

    def _post(self, wire: ProxyCompletionRequest, correlation_id: str) -> httpx.Response:
        """Perform one attempt: POST the completion request and raise on any error status.

        Kept to exactly the work that can fail transiently, because this is the unit the retry
        controller repeats.
        """
        payload: dict[str, object] = wire.model_dump(mode="json", by_alias=True)
        response = self._client.post(
            COMPLETIONS_PATH,
            json=payload,
            headers={
                CORRELATION_HEADER: correlation_id,
                # The one place in this package where the secret becomes a plain string.
                "authorization": f"Bearer {self._settings.proxy_api_key.get_secret_value()}",
            },
        )
        # Raises for 4xx and 5xx alike; _is_transient is what decides which of those come back.
        response.raise_for_status()
        return response

    def _read_completion(
        self,
        response: httpx.Response,
        correlation_id: str,
        context: CorrelationContext,
    ) -> ProxyCompletionResponse:
        """Validate the proxy's body, after checking it answered the question we asked.

        The Java ``CorrelationIdFilter`` echoes ``X-Correlation-Id`` on every response, so a
        mismatch means the response in hand belongs to a different request - a proxy bug, or a
        cache serving a crossed pair. Either way the safe move is to refuse it rather than
        attribute somebody else's answer to this taxpayer.
        """
        echoed = response.headers.get(CORRELATION_HEADER)
        if echoed is not None and echoed != correlation_id:
            _LOG.error(
                "proxy echoed a different correlation id",
                extra={
                    "event": "proxy.call.correlation_mismatch",
                    "echoed_correlation_id": echoed,
                    **context.as_log_fields(),
                },
            )
            raise ValueError(f"proxy echoed correlation id {echoed!r}, expected {correlation_id!r}")
        # model_validate_json reads the bytes directly through pydantic-core, which is faster
        # than json.loads followed by model_validate and gives better error locations.
        return ProxyCompletionResponse.model_validate_json(response.content)

    @staticmethod
    def _log_retry(state: RetryCallState) -> None:
        """Log one structured line per retry, naming the attempt and the reason."""
        outcome = state.outcome
        reason = repr(outcome.exception()) if outcome is not None and outcome.failed else None
        _LOG.warning(
            "retrying proxy call",
            extra={
                "event": "proxy.call.retry",
                "attempt": state.attempt_number,
                "reason": reason,
            },
        )


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
```

---

## Session record

What follows is the rest of the session: the orientation that preceded the two transcripts
above, the departures from the lesson's reference snippets and why, the four results that came
back red, and two later passes.

### Orientation — before writing any code

The model read the repo before scaffolding, which changed three decisions:

1. **There is no `taxcalc-api/` directory.** The lesson assumes the Java module sits in a
   sibling folder; in this repo the Java service *is* the repo root (`src/main/java/...`). The
   sidecar was created at `taxcalc-ai/` next to it, which preserves the intent.
2. **The W3 D1 proxy in this repo is `POST /v1/completions`**, served by the Java
   `LlmProxyController`, with a `CompletionRequest{prompt, model, feature}` body and a
   `CompletionResponse{model, resolvedModel, feature, inputTokens, outputTokens, text}` reply
   plus an `X-Cost-Usd` header. The reference snippet's generic `/v1/estimate` was replaced with
   the route that actually exists.
3. **`CorrelationIdFilter` echoes `X-Correlation-Id` on every response.** That is what made the
   echo-assertion in `client.py` possible; the model would not have added it from the reference
   snippet alone.

The `Taxpayer` fixture was **not** hand-written. The first cut was generated by running the
project's own compiled `TaxpayerReadModel` class through Jackson with Spring Boot's date
settings; it was later replaced by a captured live response — see the addendum at the end of this
file, and PYTHON.md for the reproduction steps.

### On the `models.py` first cut (Transcript 1)

The first cut is substantially what shipped. Five deliberate departures from the lesson's
reference `models.py`, made while writing rather than as corrections:

* `Taxpayer` mirrors this repo's real `TaxpayerReadModel` (`displayName`, `filingStatus`,
  `homeJurisdiction`, `liabilities[]`, `tags[]`) rather than the lesson's generic
  `{id, tenantId, createdAt, amount}`. `tenantId` was absent from both sides at this point; the
  addendum below records how it arrived.
* `filing_status` gets a `@field_validator` against the set the Java entity actually persists,
  in place of the lesson's `tenant-` prefix check, because this repo has no tenant on `Taxpayer`.
* Two cross-field `@model_validator(mode="after")` rules that the reference does not have: a
  liability may not exceed the amount it was computed from, and a liability may not be stamped
  earlier than the taxpayer's own `createdAt` (which is where writer/projector clock skew shows
  up first).
* `EstimateCompletion` was added so the LLM's own JSON is validated separately from the envelope
  — and deliberately carries no identifiers.
* `dict[str, Any]` was never written; `disallow_any_explicit` would have rejected it.

### On the `client.py` first cut (Transcript 2) — the one thing the model argued with the reference about

The reference client reads:

```python
@retry(
    reraise=True,
    retry=retry_if_exception_type((
        httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError,
    )),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=0.5, max=8.0),
)
```

with, inside the body:

```python
# 4xx is not retried; 5xx is retried via the @retry decorator.
if 400 <= e.response.status_code < 500:
    raise
raise
```

**That comment does not match that code.** Both branches raise the same
`httpx.HTTPStatusError`, and the decorator's predicate matches it by *type*, so a 400 is retried
three times exactly like a 503. The committed code replaces the type predicate with
`retry_if_exception(_is_transient)`, which inspects the status:

```python
def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.NetworkError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= _HTTP_SERVER_ERROR
    return False
```

and two tests pin the counts (`route.call_count == 3` on a 503, `== 1` on a 400) so the
distinction cannot regress silently.

The decorator itself was also dropped in favour of a `Retrying` controller built in `__init__`,
so `proxy_max_retries` in `settings.py` is a knob that does something rather than a setting no
code reads.

### Four things that came back red

This is the part worth keeping. Each of these was found by running the gate, not by reading.

**1. `mypy --strict` rejected every model's class line.**

```
src/taxcalc_ai/models.py:54: error: Explicit "Any" is not allowed  [explicit-any]
src/taxcalc_ai/settings.py:37: error: Explicit "Any" is not allowed  [explicit-any]
src/taxcalc_ai/client.py:253: error: Unexpected keyword argument "correlation_id"
    for "LiabilityEstimateResult"; did you mean "correlationId"?  [call-arg]
```

Twelve errors from one missing line. Without `plugins = ["pydantic.mypy"]`, mypy sees only the
synthesised `__init__(**data: Any)` — which `disallow_any_explicit` rejects — and knows nothing
about `populate_by_name`, so it demands the camelCase alias as the keyword. Adding the plugin
plus `init_typed = true` cleared all twelve.

**2. Byte-equal round-trip against the Java JSON is not achievable, and the model had assumed it
was.** Checked empirically rather than asserted:

```
>>> M(a=Decimal('120000.00')).model_dump_json()
{"a":"120000.00"}                      # Pydantic emits a STRING
>>> M.model_validate_json(b'{"a": 120000.00}').a
Decimal('120000')                      # ... and a JSON number loses its scale on the way in
```

Java emits `120000.00` as a JSON **number**. The two encodings cannot be made byte-equal without
faking one side of the fixture, and `PYTHON.md` records the real fix (`@JsonFormat(shape =
STRING)` on the Java side) as a follow-up that deserves its own PR. The conclusion drawn *from*
that at the time — that key-set equality was therefore the strongest available assertion — was
too weak, and the addendum below corrects it.

**3. A frozen + slotted dataclass does not raise `AttributeError` on an unknown attribute.**

```
>           context.corelation_id = "typo"
E   TypeError: super(type, obj): obj must be an instance or subtype of type
```

A CPython artifact of how `__setattr__` is synthesised when a dataclass is both `frozen` and
`slots`. The assertion now targets the property actually being claimed — `not hasattr(context,
"__dict__")` — and accepts either exception type on assignment.

**4. Relative imports in the test package.**

```
E   ImportError: attempted relative import with no known parent package
```

`from .conftest import JAVA_TAXPAYER_JSON` does not work in a bare test directory. Fixed by
making `tests/` a real package with an `__init__.py` and importing `from tests.conftest import
...` explicitly, rather than leaning on pytest's `sys.path` insertion to make a bare `conftest`
importable.

### Final gate

```
uv run ruff check              → All checks passed!
uv run ruff format --check     → 13 files already formatted
uv run mypy --strict src/ tests/ → Success: no issues found in 13 source files
uv run pytest -q --cov=src --cov-fail-under=85
                               → 40 passed, total coverage 98.71%
```

### What the operator rejected

**"Commit and push after each task" — the push half could not be honoured.** Every task is
committed separately on `w7d1-implementation`, but `git push` is blocked at this machine by a
corporate pre-push guard (`intuit-git-push-guard: BLOCKED [disallowed-host: github.com]`). The
model did not attempt to work around it — a guard that exists to prevent source publication to
non-approved hosts is not something to route around — and reported the blocker instead.

**"Proper java docs" — read as docstrings, since nothing here is Java.** Every module, class,
public method and non-obvious test carries one, and they document *why* rather than restating
the signature.

### Addendum — a second pass against the deliverable, line by line

Re-reading the W7 D1 brief against what shipped turned up three gaps. None of them broke a test;
all three were places where the shipped work was defensible but not what was asked for.

**1. `tenantId` existed in the brief and on neither side of the wire.** The brief names
`alias="tenantId"` explicitly. This repo's `TaxpayerReadModel` had no tenant field at all —
`tenantId` lived only on `TaxpayerEmbedding` — so "mirror the Java record" and "carry a tenantId"
were in direct conflict, and the first pass resolved it by mirroring.

The resolution was to remove the conflict rather than pick a side: `TaxpayerReadModel` gained a
`tenantId`, and `Taxpayer` gained the matching `tenant_id` with its `tenant-` prefix validator.
Three details make it a real field rather than a decorative one:

* it is **defaulted, not nullable** (`tenant-shared`), by the same mechanism and for the same
  reason `tags` was in W3 D5 — a document written before the field existed has no `tenantId` key
  in its BSON, and Spring Data's reflective population leaves the initializer standing. A null
  would have turned every pre-existing document into a boundary `ValidationError` on the Python
  side, which is a worse failure than a coarse tenant;
* the write path takes it from the **verified JWT claim**, via a second resolver deliberately
  kept separate from the existing cost-attribution `tenantOf(Jwt)`. Both read one claim, but one
  produces a billing label where a coarse bucket is a tolerable loss and the other produces a
  stored ownership attribute whose shape is a contract. One method serving both would make a
  future change to either silently change the other;
* `applyEvent` does **not** touch it, so an at-least-once Kafka redelivery cannot downgrade a
  real tenant back to the default — `TaxpayerUpdatedEvent` carries no tenant, and the projector
  re-projects an existing document in place. There is a test for exactly this.

**2. The fixture was serialiser output, not a captured response.** The brief says to run the
service and copy the response; the first pass ran the compiled class through Jackson instead.
Same bytes, different claim. It is now the verbatim body of a real `GET
/api/v1/taxpayers/taxpayer-001` — PYTHON.md has the reproduction steps, including the two things
that make that run non-obvious (the `loadtest` profile is the only way to hold a valid token
without an IdP, and the captured `tenantId` reads `tenant-shared` because the response came back
through the Postgres fallback projection rather than the write-through).

**3. The round-trip assertion was weaker than the brief, and weaker than it needed to be.** The
brief asks for byte-equality *after* `json.loads`. The first pass concluded that the
number-vs-string money encoding made that unreachable and settled for key-set equality — which
conflated two different claims. Byte-equality is genuinely unreachable; document equality after
parsing is not. Parsing both sides with `parse_float=Decimal` and coercing the string form back
to `Decimal` gives a full value-level comparison of every key and every value in both
directions, with no change to the fixture.

One claim written while tightening that test was itself wrong and worth recording. The first
version asserted that a money field keeps its scale across the wire:

```
>>> Taxpayer.model_validate_json(fixture).liabilities[0].taxable_amount.as_tuple().exponent
0     # not -2 — Jackson's 120000.00 arrives as Decimal('120000')
```

A JSON number's trailing zeros survive no parser, Pydantic's included. What crosses exactly is
the **value** (`0.07` is `Decimal('0.07')`, never `0.07000000000000001`), and that is what the
replacement test asserts. Scale is re-imposed by `setScale(2, HALF_UP)` on the Java side and
bounded by `decimal_places=2` here — it is not something the wire preserves.

---

### Addendum 2 — the retry controller went back to being a decorator

Turn 4 above records dropping the reference's `@retry` decorator in favour of a `Retrying`
controller built in `__init__`, on the grounds that a decorator freezes `proxy_max_retries` at
import time. That reasoning was half right, and the shipped code no longer matches it — the
turn record is left standing as written, and this is the correction.

The controller did make the budget tunable, but it paid for it by moving the retry policy away
from the function it governs: `_post` read like an ordinary method, and nothing at the call
site said it would be attempted three times. `retry_with()` gets both. The policy is declared
as a decorator directly above `_post`, where anyone reading that function sees it, and the call
site copies it with one field overridden:

```python
attempt = LlmProxyClient._post.retry_with(stop=stop_after_attempt(self._settings.proxy_max_retries))
response = attempt(self, wire=wire, context=context)
```

At the default of 3 the copy is identical to the declared policy. The copy wraps the
*undecorated* function and is therefore unbound, which is why `self` is passed explicitly — a
detail worth writing down, because the version that omits it fails at runtime rather than under
mypy.

The same pass fixed something the first cut got wrong and no test had caught: `proxy.call.retry`
was the only event in the log stream with no `correlation_id` on it. The retry logger was a
`@staticmethod` with no access to the `CorrelationContext`, so the one line you would most want
to filter on during a retry storm was the one line you could not filter. `_log_retry` is now a
module function that reads the context back off `RetryCallState.kwargs` — which is why `_post`
takes keyword-only arguments, so the lookup is by name rather than by an argument position that
would shift under any signature change. `httpx`'s own per-request INFO line, which carries no
ids either, is raised to WARNING rather than silenced, so its transport warnings still surface.

`tests/test_client.py::test_retry_log_line_carries_correlation_and_tenant` pins it: one 503
followed by a 200, and the single `proxy.call.retry` record must carry the correlation id, the
tenant id, the attempt number, the status in its reason, and no API key.

### Gate after this pass

```
uv run ruff check                → All checks passed!
uv run ruff format --check       → 15 files already formatted
uv run mypy --strict src/ tests/ → Success: no issues found in 13 source files
uv run pytest -v --cov=src --cov-fail-under=85
                                 → 43 passed, total coverage 98.78%
```
