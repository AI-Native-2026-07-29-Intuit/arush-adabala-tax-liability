# taxcalc-mcp-server/tests/test_numeric.py
"""The numeric discipline, tested where it is now stated once.

:mod:`taxcalc_mcp_server.numeric` is the one place this package names the inexact binary type,
so that the tool modules never do - which is what lets the W7 D4 money gate grep ``tools/`` for
that name and legitimately expect nothing. Centralising it that way only helps if the central
version is right, so each of the three kinds of number gets its own assertions here rather than
being covered incidentally through a tool call.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from taxcalc_mcp_server.numeric import (
    MINOR_UNITS_PER_MAJOR,
    as_minor_units,
    as_token_count,
    is_inexact_binary,
    to_score,
)

# ---- Money: the type it must never be -------------------------------------------------------


@pytest.mark.parametrize("value", [0.1, 10.0, -1.5, 0.0, 1e-9])
def test_inexact_binary_values_are_detected(value: object) -> None:
    """The predicate catches exactly what must never reach a money field."""
    assert is_inexact_binary(value) is True


@pytest.mark.parametrize("value", ["10.00", Decimal("10.00"), 10, None, True, [], "abc"])
def test_everything_else_is_not_flagged(value: object) -> None:
    """A string, a Decimal or an int is an acceptable money input; the predicate stays narrow.

    ``bool`` is deliberately not flagged here: it is an ``int`` subclass, not an inexact binary
    one, and a boolean in a money field fails the ``Decimal`` parse on its own terms. Flagging it
    would report the wrong reason for the right rejection.
    """
    assert is_inexact_binary(value) is False


def test_the_predicate_is_why_the_money_rule_is_enforceable() -> None:
    """``Decimal(0.1)`` is the number this rule exists to keep out of a ledger.

    RUF032 - "Decimal() called with float literal" - is exactly the defect under test here, so
    it is silenced on these two lines and nowhere else. Rewriting them to satisfy the linter
    would delete the demonstration and leave the rule asserted only by assertion.
    """
    assert Decimal(0.1) != Decimal("0.1")  # noqa: RUF032 - the defect being demonstrated
    assert str(Decimal(0.1)).startswith("0.1000000000000000055")  # noqa: RUF032 - ditto


# ---- Measurements: the type it correctly is -------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.5, 0.5), (1, 1.0), (0, 0.0), ("0.75", 0.75), (Decimal("0.25"), 0.0)],
)
def test_scores_coerce_or_default(value: object, expected: float) -> None:
    """Scores parse from numbers and numeric strings, and default rather than raising.

    ``Decimal`` defaults rather than converting on purpose: a pipeline handing back a Decimal
    score is doing something unexpected, and the tolerant path here is for *missing* data, not
    for quietly accepting a type nothing in the retrieval stack produces.
    """
    assert to_score(value) == expected


@pytest.mark.parametrize("value", [None, "", "not-a-number", [], {}, True])
def test_unusable_scores_take_the_default(value: object) -> None:
    """A malformed score costs one field, never the whole grounded answer above it."""
    assert to_score(value) == 0.0
    assert to_score(value, 0.5) == 0.5


# ---- Counts: summed, so integer ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(11, 11), ("11", 11), (11.7, 11), ("11.7", 11), (0, 0)],
)
def test_token_counts_coerce_from_both_dialects(value: object, expected: int) -> None:
    """The two proxy dialects disagree about numbers vs strings; both yield an int."""
    assert as_token_count(value) == expected


@pytest.mark.parametrize("value", [None, "", "abc", [], {}, True, False])
def test_unusable_token_counts_are_zero(value: object) -> None:
    """A log-and-display field is never worth failing a reply the caller was billed for.

    ``True`` is zero rather than one: a boolean in a token count is an upstream bug, and
    recording it as a single token would hide it behind a plausible number.
    """
    assert as_token_count(value) == 0


def test_cost_header_becomes_integer_minor_units() -> None:
    """A major-unit decimal string is stored as an integer count of minor units.

    Integer because this number is summed across every request in a dashboard.
    """
    assert as_minor_units("0.42") == 42
    assert as_minor_units("1.00") == MINOR_UNITS_PER_MAJOR
    assert as_minor_units("0.0042") == 0  # truncated: below one minor unit


@pytest.mark.parametrize("header", [None, "", "not-a-price", "NaN"])
def test_a_missing_or_malformed_cost_header_is_zero(header: str | None) -> None:
    """The proxy failing to price a call is a question for its metrics, not an error here."""
    assert as_minor_units(header) == 0


def test_minor_units_never_round_trip_through_an_inexact_type() -> None:
    """The parse goes through ``Decimal``, so a value with no exact binary form lands exactly.

    ``0.29`` cannot be represented in binary: the naive ``0.29 * 100`` is
    ``28.999999999999996``, and truncating that gives **28 cents instead of 29**. One unit, lost
    silently, on a value a human would have called exact - summed across a month of requests,
    that is the whole reason this helper parses through ``Decimal`` first.
    """
    assert as_minor_units("0.29") == 29
    # The bug being avoided, asserted so the reason stays visible rather than becoming folklore.
    assert int(0.29 * 100) == 28
