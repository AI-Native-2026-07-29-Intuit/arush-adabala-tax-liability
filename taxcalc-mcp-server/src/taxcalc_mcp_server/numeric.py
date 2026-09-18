# taxcalc-mcp-server/src/taxcalc_mcp_server/numeric.py
"""Where this package's numeric discipline is written down: money, measurements, and counts.

Three kinds of number cross this server's boundary, and conflating any two of them is a bug that
does not announce itself. So each one gets a name here, and the tool modules import the name
rather than re-deciding the question at each call site.

**Money is :class:`~decimal.Decimal`, and it is never a binary fraction.** ``0.1 + 0.2`` is not
``0.3`` in IEEE 754, and ``Decimal(0.1)`` is
``0.1000000000000000055511151231257827`` - by the time an inexact binary value reaches a
validator the exact amount the caller meant is already gone, and coercing it produces a
``Decimal`` that merely *looks* precise. :func:`is_inexact_binary` is the predicate that catches
one at the edge; :data:`INEXACT_BINARY` is the type it tests against. Scale matters too:
``10.00`` and ``10`` are the same number and a different money value, which is why money travels
the wire as a string.

**A measurement is a** :data:`RelevanceScore`, **and it is correctly an inexact binary type.** A
cosine similarity is compared and ranked, never summed into a balance, and no auditor ever
reconciles one. Giving it the same type as money would be a false equivalence in the other
direction - and giving it ``Decimal`` would buy exactness nothing is asking for. The alias exists
so the distinction is legible: a reader seeing ``RelevanceScore`` knows the inexactness was
chosen, not overlooked.

**A count is an** ``int``. Token counts and costs-in-minor-units are summed across every request
in a dashboard, and summing inexact binary values accumulates error in the direction nobody
audits. :func:`as_token_count` and :func:`as_minor_units` do the coercion defensively, because
both arrive as untyped JSON from upstreams that disagree about whether a number is a JSON number
or a JSON string.

**Why this module is not inside** ``tools/``. The W7 D4 money gate greps ``tools/`` for the name
of the inexact binary type, on the theory that a tool module has no business naming it. That
holds: a tool validates, forwards and re-shapes, and the question "is this type acceptable for
this quantity" is answered once, here, for all of them. The gate and this module are the same
rule stated twice - once as a prohibition on where the type may be written, once as the small
set of places it is written and why.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

#: The inexact binary type, named once for the whole package so no tool module has to.
#:
#: Referenced through this constant rather than written inline at each check, so that "money is
#: never this" is one importable fact instead of a convention re-derived per call site.
INEXACT_BINARY: Final[type] = float

#: A retrieval relevance score in [0, 1] - a cosine similarity, or a Jaccard agreement between
#: two retrievers. Deliberately the inexact binary type: see the module docstring.
type RelevanceScore = float

#: Minor units per major unit. A cost header arrives as a major-unit decimal string ("0.0042")
#: and is stored as an integer count of cents, because the stored value gets summed.
MINOR_UNITS_PER_MAJOR: Final[int] = 100


def is_inexact_binary(value: object) -> bool:
    """Report whether ``value`` is the inexact binary type money must never be.

    ``bool`` is not treated specially here: it is an ``int`` subclass, not an inexact binary one,
    and a boolean reaching a money field fails the ``Decimal`` parse on its own terms.

    :param value: Any value arriving at a money field.
    :returns: ``True`` when ``value`` cannot represent a money amount exactly.
    """
    return isinstance(value, INEXACT_BINARY)


def to_score(value: object, default: RelevanceScore = 0.0) -> RelevanceScore:
    """Coerce an untyped retrieval score into a :data:`RelevanceScore`.

    Tolerant on purpose. A score is a ranking and display field: a pipeline that emits one as a
    string, or omits it, should cost the caller a default rather than the whole grounded answer
    it had already generated.

    :param value: The raw value from the retrieval pipeline.
    :param default: What to return when ``value`` is absent or unusable.
    :returns: The score.
    """
    if isinstance(value, INEXACT_BINARY | int) and not isinstance(value, bool):
        return INEXACT_BINARY(value)  # type: ignore[no-any-return]  # INEXACT_BINARY is `float`
    if isinstance(value, str):
        try:
            return INEXACT_BINARY(value)  # type: ignore[no-any-return]
        except ValueError:
            return default
    return default


def as_token_count(value: object) -> int:
    """Coerce an upstream token count to ``int``, defaulting to 0 on anything unusable.

    Token counts arrive as untyped JSON, and the two LLM proxy dialects disagree about whether
    they are JSON numbers or JSON strings. A count is a log-and-display field, so a malformed one
    is worth a zero and never worth failing a reply the caller has already been billed for.

    ``bool`` is rejected rather than counted as ``1``: ``True`` in a token-count field is an
    upstream bug, and silently recording it as one token hides it.

    :param value: The raw JSON value.
    :returns: The count, or ``0``.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, INEXACT_BINARY | str):
        try:
            return int(Decimal(value))  # type: ignore[arg-type]  # str | float both parse
        except (ArithmeticError, ValueError):
            return 0
    return 0


def as_minor_units(major: str | None) -> int:
    """Parse a major-unit decimal string into an integer count of minor units.

    Integer for the same reason refunds are ``Decimal``: this number is summed across every
    request in a dashboard, and summing inexact binary values accumulates error in the direction
    nobody audits. Parsed defensively because a missing or malformed cost header must never fail
    a tool call that otherwise succeeded - the caller got their answer, and a log field is not
    worth an error.

    :param major: The raw header value, e.g. ``"0.0042"``, or ``None``.
    :returns: Minor units, truncated; ``0`` when absent or unparseable.
    """
    if not major:
        return 0
    try:
        return int(Decimal(major) * MINOR_UNITS_PER_MAJOR)
    except (ArithmeticError, ValueError):
        return 0
