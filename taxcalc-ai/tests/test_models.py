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
    """Pydantic must read the JSON the Java side emits and write JSON it can read back.

    The fixture is real Jackson output: money is a JSON **number** with its scale preserved
    (``120000.00``), because Spring Boot serialises ``BigDecimal`` that way. Pydantic reads that
    into a ``Decimal`` losslessly in value, but re-emits a ``Decimal`` as a JSON *string*, and
    a JSON number's trailing zeros are not preserved through parsing at all. So a literal
    byte-for-byte comparison of the two encodings is not achievable in either direction, and
    asserting it would only be achievable by faking one side of the fixture.

    What is asserted instead is the contract that actually matters:

    1. every key the Java side emits is consumed (``extra="forbid"`` would reject a stray one);
    2. every key Pydantic emits is one the Java side emits - the alias map is complete, with no
       snake_case leaking onto the wire;
    3. the round-trip is value-preserving: re-validating our own output reproduces an equal
       model, money included.

    See PYTHON.md, "Boundary contract", for the follow-up: moving the Java side to string money
    would make the encodings identical, and is the only thing standing between this and a
    byte-equality assertion.
    """
    parsed = Taxpayer.model_validate_json(java_taxpayer_json)

    ours = parsed.model_dump_json(by_alias=True)
    java_doc = json.loads(java_taxpayer_json)
    our_doc = json.loads(ours)

    assert our_doc.keys() == java_doc.keys()
    assert our_doc["liabilities"][0].keys() == java_doc["liabilities"][0].keys()

    again = Taxpayer.model_validate_json(ours.encode())
    assert again == parsed

    # Value equality across the two encodings, stated explicitly for the money fields.
    assert parsed.liabilities[0].taxable_amount == Decimal(
        str(java_doc["liabilities"][0]["taxableAmount"])
    )
    assert parsed.created_at == datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    assert parsed.tags == ("w7d1", "synthetic")
