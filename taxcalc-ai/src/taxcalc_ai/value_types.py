# taxcalc-ai/src/taxcalc_ai/value_types.py
"""Internal value types - frozen dataclasses with slots.

These never cross an external boundary; that is what the Pydantic models in
:mod:`taxcalc_ai.models` are for. Because nothing untrusted reaches them, they pay for no
validation machinery: ``@dataclass(frozen=True, slots=True)`` gives value equality, hashability
and a fixed field set at roughly the cost of a plain tuple.

``slots=True`` is not only a memory optimisation here. It removes ``__dict__``, so a typo
(``ctx.corelation_id = x``) raises ``AttributeError`` instead of quietly creating a second
attribute nothing reads - the frozen-dataclass equivalent of ``extra="forbid"``.

Collection fields are ``tuple``, never ``list``: a frozen dataclass holding a list is frozen
only at the top level, and it is also unhashable, which would defeat ``ProxyCallKey``'s whole
purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ProxyCallKey:
    """Identity of one in-flight proxy call. Hashable by value.

    Used as a dict key for de-duplication and for tagging a call in logs. The prompt is held as
    a hash rather than as text so the key stays small and so a key that ends up in a log line
    or a metric label never carries taxpayer detail with it.
    """

    correlation_id: str
    model_id: str
    prompt_hash: str

    def __post_init__(self) -> None:
        """Reject empty components, which would collapse distinct calls onto one key."""
        for name in ("correlation_id", "model_id", "prompt_hash"):
            if not getattr(self, name):
                raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """The W3 D2 correlation-id carrier, propagated through structured logs.

    Everything that identifies *where a call came from* travels together in one object rather
    than as three loose string arguments, so a new dimension (a request id, a user id) is added
    in one place instead of threaded through every signature.
    """

    correlation_id: str
    tenant_id: str
    started_at: datetime
    # Tuple, not list - immutability is end to end, and it keeps this class hashable.
    tags: tuple[str, ...] = field(default=())

    def as_log_fields(self) -> dict[str, str]:
        """Render this context as the flat key/value pairs a structured log line carries.

        Returned as a fresh dict each call: handing out a shared mapping would let one log
        site's mutation leak into the next.
        """
        fields: dict[str, str] = {
            "correlation_id": self.correlation_id,
            "tenant_id": self.tenant_id,
            "started_at": self.started_at.isoformat(),
        }
        if self.tags:
            fields["tags"] = ",".join(self.tags)
        return fields
