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

#: Wire keys whose values are money. Java emits them as JSON numbers, Pydantic as JSON
#: strings; :func:`_decimalised` brings both to ``Decimal`` before any comparison.
MONEY_KEYS = frozenset({"taxableAmount", "liabilityAmount", "estimatedLiability"})


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


def _decimalised(raw: bytes) -> dict[str, object]:
    """Parse JSON with every money value as a ``Decimal``, whichever way it was encoded.

    The two sides encode money differently and legitimately so: Jackson writes a JSON number
    (``120000.00``), Pydantic writes a JSON string (``"120000.00"``). ``parse_float=Decimal``
    handles the first, ``MONEY_KEYS`` coercion handles the second, and after both the documents
    are comparable as values instead of as bytes.

    ``parse_float=Decimal`` is what makes the number side lossless: ``json.loads`` would
    otherwise hand back the binary float ``120000.0``, which is the exact precision loss the
    BigDecimal/Decimal rule on both sides of this wire exists to prevent.
    """
    doc = json.loads(raw, parse_float=Decimal)
    assert isinstance(doc, dict)
    return _coerce_money(doc)  # type: ignore[return-value]


def _coerce_money(node: object) -> object:
    """Recursively rewrite every :data:`MONEY_KEYS` value in ``node`` to a ``Decimal``."""
    if isinstance(node, dict):
        return {
            key: Decimal(value)
            if key in MONEY_KEYS and isinstance(value, str)
            else _coerce_money(value)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_coerce_money(item) for item in node]
    return node


def test_round_trip_against_java_json(java_taxpayer_json: bytes) -> None:
    """Pydantic must read the JSON the Java side emits and write JSON it can read back.

    The fixture is the verbatim response body of a real ``GET
    /api/v1/taxpayers/taxpayer-001`` against a locally-running taxcalc-api (see PYTHON.md for
    the capture procedure), so what is asserted here is the live contract and not a
    Python-shaped idea of it.

    The whole document is compared, key by key and value by value - not merely the key sets.
    Two encoding differences stand between the raw bytes and that comparison, and both are
    normalised rather than papered over:

    * money crosses as a JSON **number** from Jackson and as a JSON **string** from Pydantic;
    * a JSON number's trailing zeros survive no parser, so ``120000.00`` read naively becomes
      the float ``120000.0``.

    :func:`_decimalised` handles both by parsing with ``parse_float=Decimal`` and coercing the
    string form back to ``Decimal``. That is the strongest true statement available about these
    two encodings: byte equality itself is unreachable while the Java side writes numbers, but
    *value* equality across the full document is, and it is what actually protects the contract.
    """
    parsed = Taxpayer.model_validate_json(java_taxpayer_json)
    ours = parsed.model_dump_json(by_alias=True)

    # The contract, in one assertion: every key and every value, both directions.
    assert _decimalised(ours.encode()) == _decimalised(java_taxpayer_json)

    # And the model survives its own output - no alias or coercion is lossy in between.
    assert Taxpayer.model_validate_json(ours.encode()) == parsed

    # Spot-checks that name the values, so a failure above says which one moved.
    java_doc = _decimalised(java_taxpayer_json)
    liabilities = java_doc["liabilities"]
    assert isinstance(liabilities, list)
    assert parsed.tenant_id == java_doc["tenantId"]
    taxable = liabilities[0]["taxableAmount"]
    assert parsed.liabilities[0].taxable_amount == taxable == Decimal("120000.00")
    assert parsed.created_at == datetime(2026, 9, 14, 23, 45, 33, 690400, tzinfo=UTC)


def test_java_money_crosses_the_wire_without_binary_float_error() -> None:
    """Money arrives exact, never as the nearest binary float.

    This is the precision guarantee the Decimal rule on both sides exists for. Note what is
    and is not preserved: the **value** is exact, but a JSON number's trailing zeros are not -
    Jackson's ``120000.00`` parses to ``Decimal('120000')``, scale 0. So the two sides agree on
    what a liability is worth, not on how many zeros were typed; scale is re-imposed where it
    matters, by Java's ``setScale(2, HALF_UP)`` on the way out of a calculation.

    The amounts here are chosen to fail loudly under a float parser: ``1234567.89`` becomes
    ``1234567.8899999999`` and ``0.07`` becomes ``0.07000000000000001``.
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
