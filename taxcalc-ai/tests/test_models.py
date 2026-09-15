# taxcalc-ai/tests/test_models.py
"""Boundary-model tests: the contract with the Java side, and the validators that guard it."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from taxcalc_ai.models import (
    EstimateCompletion,
    Liability,
    LiabilityEstimateRequest,
    LiabilityEstimateResult,
    Taxpayer,
)


def _liability_kwargs() -> dict[str, object]:
    """Minimal valid keyword arguments for :class:`Liability`."""
    return {
        "tax_year": 2026,
        "bracket_id": "bracket-ca-2026-mid",
        "taxable_amount": Decimal("120000.00"),
        "liability_amount": Decimal("26400.00"),
        "computed_at": datetime(2026, 1, 15, 12, 0, 5, tzinfo=UTC),
    }


def test_taxpayer_rejects_unknown_filing_status() -> None:
    """A typo'd filing status must fail at the boundary, not at bracket resolution."""
    with pytest.raises(ValidationError) as excinfo:
        Taxpayer(
            id="taxpayer-001",
            tenant_id="tenant-shared",
            display_name="Ada Lovelace",
            filing_status="SINGEL",
            home_jurisdiction="CALIFORNIA",
            created_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        )
    assert "filing_status must be one of" in str(excinfo.value)


def test_taxpayer_forbids_extra_fields() -> None:
    """``extra="forbid"`` turns a Java-side field rename into a loud failure here."""
    with pytest.raises(ValidationError) as excinfo:
        Taxpayer(
            id="taxpayer-001",
            tenant_id="tenant-shared",
            display_name="Ada Lovelace",
            filing_status="SINGLE",
            home_jurisdiction="CALIFORNIA",
            created_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
            surpriseField="nope",  # type: ignore[call-arg]
        )
    assert "surpriseField" in str(excinfo.value)


def test_taxpayer_is_frozen_all_the_way_down(taxpayer: Taxpayer) -> None:
    """Freezing is end to end: no attribute assignment, and no mutable collection fields."""
    with pytest.raises(ValidationError):
        taxpayer.display_name = "Grace Hopper"  # type: ignore[misc]
    assert isinstance(taxpayer.tags, tuple)
    assert isinstance(taxpayer.liabilities, tuple)
    assert not hasattr(taxpayer.tags, "append")


@pytest.mark.parametrize(
    ("amount", "ok"),
    [
        (Decimal("0"), True),
        (Decimal("0.01"), True),
        (Decimal("-0.01"), False),
        (Decimal("-1"), False),
        (Decimal("0.001"), False),  # more than 2 decimal places
    ],
)
def test_liability_amount_must_be_non_negative_and_2dp(amount: Decimal, ok: bool) -> None:
    """Money is bounded at zero and at 2 decimal places, matching Java's setScale(2, HALF_UP)."""
    kwargs = {**_liability_kwargs(), "taxable_amount": amount, "liability_amount": amount}
    if ok:
        assert Liability(**kwargs).taxable_amount == amount  # type: ignore[arg-type]
    else:
        with pytest.raises(ValidationError):
            Liability(**kwargs)  # type: ignore[arg-type]


def test_liability_cannot_exceed_taxable_amount() -> None:
    """A cross-field invariant no single-field validator could express."""
    kwargs = {**_liability_kwargs(), "liability_amount": Decimal("999999.00")}
    with pytest.raises(ValidationError) as excinfo:
        Liability(**kwargs)  # type: ignore[arg-type]
    assert "exceeds" in str(excinfo.value)


def test_liability_computed_before_taxpayer_created_is_rejected() -> None:
    """Clock skew between the writer and the projector surfaces here first."""
    early = {**_liability_kwargs(), "computed_at": datetime(2025, 1, 1, tzinfo=UTC)}
    with pytest.raises(ValidationError) as excinfo:
        Taxpayer(
            id="taxpayer-001",
            tenant_id="tenant-shared",
            display_name="Ada Lovelace",
            filing_status="SINGLE",
            home_jurisdiction="CALIFORNIA",
            created_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
            liabilities=(Liability(**early),),  # type: ignore[arg-type]
        )
    assert "before the taxpayer was created" in str(excinfo.value)


def test_request_correlation_id_must_carry_the_prefix(taxpayer: Taxpayer) -> None:
    """The ``corr-`` prefix is what makes the id recognisable among other opaque ids."""
    with pytest.raises(ValidationError) as excinfo:
        LiabilityEstimateRequest(correlation_id="w7d1-0001", taxpayer=taxpayer)
    assert "must start with 'corr-'" in str(excinfo.value)


def test_result_high_confidence_requires_long_rationale() -> None:
    """A confident answer with an unauditable rationale is refused at the boundary."""
    with pytest.raises(ValidationError) as excinfo:
        LiabilityEstimateResult(
            correlation_id="corr-1",
            taxpayer_id="taxpayer-001",
            label="standard",
            confidence=0.95,
            rationale="short",  # < 16 chars
            estimated_liability=Decimal("26400.00"),
            model_id="claude-haiku-4-5",
        )
    assert "requires a rationale" in str(excinfo.value)


def test_result_low_confidence_allows_short_rationale() -> None:
    """The same short rationale is fine below the high-confidence threshold."""
    result = LiabilityEstimateResult(
        correlation_id="corr-1",
        taxpayer_id="taxpayer-001",
        label="standard",
        confidence=0.4,
        rationale="short",
        estimated_liability=Decimal("26400.00"),
        model_id="claude-haiku-4-5",
    )
    assert result.confidence == pytest.approx(0.4)


def test_estimate_completion_forbids_identifier_smuggling() -> None:
    """The model's own JSON may not carry identifiers; the client composes those itself."""
    with pytest.raises(ValidationError):
        EstimateCompletion.model_validate_json(
            b'{"label":"standard","confidence":0.5,"rationale":"ok",'
            b'"estimatedLiability":"1.00","taxpayerId":"taxpayer-999"}'
        )


def test_round_trip_against_java_json(java_taxpayer_json: bytes) -> None:
    """Pydantic reads the JSON the Java side emits and writes back the same document.

    The fixture is the response body of a real ``GET /api/v1/taxpayers/taxpayer-001`` against a
    locally-running taxcalc-api, re-emitted through ``TaxpayerReadModel`` after the W7 D1
    ``@JsonFormat(shape = STRING)`` change (see PYTHON.md, "The round-trip fixture"). So what is
    asserted here is the live contract, not a Python-shaped idea of it.

    **This is a whole-document equality assertion, and it only became possible today.** While
    Jackson wrote money as a bare JSON number the two encodings could not be reconciled:
    Pydantic emits a ``Decimal`` as a JSON string, and a JSON number's trailing zeros survive no
    parser, so ``120000.00`` arrived as ``Decimal('120000')`` - scale gone. The test used to
    normalise both sides to ``Decimal`` before comparing, which was the strongest true statement
    available but weaker than the contract deserved.

    Annotating the Java money fields with ``@JsonFormat(shape = STRING)`` removed the seam
    rather than working around it. Both sides now write ``"120000.00"`` verbatim, so the
    documents compare directly - no normalisation, no coercion, nothing for a future bug to
    hide behind. Key ordering is the only remaining difference, which is what comparing parsed
    documents rather than raw bytes accounts for (Pydantic emits ``tenantId`` in field-
    declaration order, Jackson at the end).
    """
    parsed = Taxpayer.model_validate_json(java_taxpayer_json)
    ours = parsed.model_dump_json(by_alias=True)

    # The contract, in one assertion: every key, every value, no normalisation.
    assert json.loads(ours) == json.loads(java_taxpayer_json)

    # And the model survives its own output - no alias or coercion is lossy in between.
    assert Taxpayer.model_validate_json(ours.encode()) == parsed

    # Scale is the thing the string encoding buys, so name it explicitly: a plain JSON number
    # would have made this Decimal('120000'), exponent 0.
    taxable = parsed.liabilities[0].taxable_amount
    assert taxable == Decimal("120000.00")
    assert taxable.as_tuple().exponent == -2

    # Spot-checks that name the values, so a failure above says which one moved.
    java_doc = json.loads(java_taxpayer_json)
    assert parsed.tenant_id == java_doc["tenantId"]
    assert parsed.created_at == datetime(2026, 9, 14, 23, 45, 33, 690400, tzinfo=UTC)


def test_java_money_crosses_the_wire_without_binary_float_error() -> None:
    """Money arrives exact, never as the nearest binary float - even as a bare JSON number.

    The wire format is now a JSON string end to end
    (``@JsonFormat(shape = STRING)``), so this is no longer the path the taxcalc-api response
    takes. It is kept deliberately: a JSON *number* is still what an older cached payload, a
    replayed event, or any other producer might hand this model, and the safe thing on that
    path is to parse it exactly rather than through a float.

    The amounts are chosen to fail loudly under a float parser: ``1234567.89`` becomes
    ``1234567.8899999999`` and ``0.07`` becomes ``0.07000000000000001``. What a JSON number
    cannot carry is *scale* - trailing zeros are gone before any parser sees them - which is
    precisely why the wire moved to strings.
    """
    raw = json.dumps(
        {
            "taxYear": 2026,
            "bracketId": "fed-2026-22pct",
            "taxableAmount": 1234567.89,
            "liabilityAmount": 0.07,
            "computedAt": "2026-09-14T23:46:45.250267Z",
        }
    ).encode()

    parsed = Liability.model_validate_json(raw)

    assert parsed.taxable_amount == Decimal("1234567.89")
    assert parsed.liability_amount == Decimal("0.07")
    # Exact, not merely close: == on Decimal and float would coerce and hide the difference.
    assert str(parsed.taxable_amount) == "1234567.89"


def test_taxpayer_rejects_an_unprefixed_tenant_id(java_taxpayer_json: bytes) -> None:
    """The ``tenant-`` prefix is a contract both sides assert; a bare tenant is a boundary error."""
    doc = json.loads(java_taxpayer_json)
    doc["tenantId"] = "shared"

    with pytest.raises(ValidationError) as excinfo:
        Taxpayer.model_validate_json(json.dumps(doc).encode())

    assert "tenant_id must start with 'tenant-'" in str(excinfo.value)
