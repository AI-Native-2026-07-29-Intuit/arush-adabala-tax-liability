# taxcalc-ai/src/taxcalc_ai/models.py
"""Pydantic v2 boundary models for the taxcalc-api sidecar.

These are the only types allowed to touch bytes that came from outside this process. Every
one of them is ``extra="forbid"`` and ``frozen=True``: forbidding extras means a Java-side
field rename surfaces here as a loud ``ValidationError`` at the boundary rather than as a
silently-dropped key that turns into a wrong number three call frames later, and freezing
means a validated object cannot be edited afterwards into a state validation never saw.

``Taxpayer`` mirrors the Java ``TaxpayerReadModel`` exactly, including its camelCase wire
names. The Java side serialises through Spring Boot's Jackson defaults, so:

* keys are camelCase (``displayName``, ``tenantId``, ``createdAt``, ...) - hence ``alias=``
  plus ``populate_by_name=True``, so Python code can construct with snake_case while the wire
  form stays camelCase;
* ``Instant`` is an ISO-8601 string ending in ``Z`` - which is exactly what Pydantic emits for
  a UTC-aware ``datetime``;
* ``BigDecimal`` money is a JSON **number**, not a string.

That last point is the one real seam between the two languages, and
``tests/test_models.py::test_round_trip_against_java_json`` closes it: Pydantic reads the Java
number into a ``Decimal`` with no binary-float error, but re-emits it as a JSON string, so the
two encodings are compared as parsed values rather than as bytes. The comparison is of the
whole document - every key and every value, both directions - not merely of the key sets. See
that test and ``PYTHON.md`` for what a JSON number does and does not preserve.

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

#: Required prefix on a tenant id, mirroring ``TaxpayerReadModel.TENANT_ID_PREFIX`` on the Java
#: side. Both ends assert it rather than one assuming it of the other.
TENANT_ID_PREFIX: Final[str] = "tenant-"

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
    tenant_id: str = Field(min_length=1, max_length=64, alias="tenantId")
    display_name: str = Field(min_length=1, max_length=255, alias="displayName")
    filing_status: str = Field(min_length=1, alias="filingStatus")
    home_jurisdiction: str = Field(min_length=1, max_length=64, alias="homeJurisdiction")
    created_at: datetime = Field(alias="createdAt")
    liabilities: tuple[Liability, ...] = ()
    tags: tuple[str, ...] = ()

    @field_validator("tenant_id")
    @classmethod
    def _tenant_id_shape(cls, v: str) -> str:
        """Require the ``tenant-`` prefix the Java document guarantees.

        A tenant id, a taxpayer id and a bracket id are all opaque strings, and the prefix is
        what tells them apart in a log line. Asserting it here rather than assuming it means a
        Java-side change to the ownership model surfaces as a boundary failure on the first
        request, not as a mis-scoped query somewhere downstream.
        """
        if not v.startswith(TENANT_ID_PREFIX):
            raise ValueError(f"tenant_id must start with '{TENANT_ID_PREFIX}'")
        return v

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
