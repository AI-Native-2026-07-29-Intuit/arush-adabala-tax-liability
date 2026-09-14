# taxcalc-ai/tests/test_value_types.py
"""Value-type tests - the properties frozen+slots dataclasses are chosen for."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from taxcalc_ai.value_types import CorrelationContext, ProxyCallKey


def _key() -> ProxyCallKey:
    return ProxyCallKey(correlation_id="corr-1", model_id="claude-haiku-4-5", prompt_hash="abc123")


def test_proxy_call_key_is_hashable_by_value() -> None:
    """Two keys with the same components are the same key - the point of using one as a key."""
    assert _key() == _key()
    assert len({_key(), _key()}) == 1


def test_proxy_call_key_rejects_empty_components() -> None:
    """An empty component would collapse distinct calls onto one key."""
    with pytest.raises(ValueError, match="prompt_hash must not be empty"):
        ProxyCallKey(correlation_id="corr-1", model_id="claude-haiku-4-5", prompt_hash="")


def test_slots_reject_a_typo_instead_of_silently_creating_an_attribute() -> None:
    """``slots=True`` is the frozen-dataclass equivalent of ``extra="forbid"``.

    The absence of ``__dict__`` is the property being asserted; the exception type raised on
    assignment is an implementation artifact of how CPython synthesises ``__setattr__`` for a
    dataclass that is both frozen and slotted, so both spellings are accepted here.
    """
    context = CorrelationContext(
        correlation_id="corr-1", tenant_id="tenant-a", started_at=datetime.now(tz=UTC)
    )
    assert not hasattr(context, "__dict__")
    with pytest.raises((AttributeError, TypeError)):
        context.corelation_id = "typo"  # type: ignore[attr-defined]


def test_correlation_context_log_fields_are_a_fresh_mapping() -> None:
    """Handing out a shared mapping would let one log site's mutation leak into the next."""
    context = CorrelationContext(
        correlation_id="corr-1",
        tenant_id="tenant-a",
        started_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        tags=("liability-estimate", "w7d1"),
    )

    first = context.as_log_fields()
    first["correlation_id"] = "mutated"

    assert context.as_log_fields()["correlation_id"] == "corr-1"
    assert context.as_log_fields()["tags"] == "liability-estimate,w7d1"
    assert context.as_log_fields()["started_at"] == "2026-01-15T12:00:00+00:00"
