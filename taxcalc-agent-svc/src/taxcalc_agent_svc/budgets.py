# taxcalc-agent-svc/src/taxcalc_agent_svc/budgets.py
"""Per-request cost ceiling: the SLOW half of the runaway defence.

Cumulative spend is checked *before* every Claude call and updated *after* it. A breach raises
:class:`BudgetExceeded`, which the FastAPI handler maps to HTTP 503 with a ``Retry-After``
header, and which the SSE bridge emits as a distinct ``3:`` error event so the React client can
tell "we ran out of money" from "we ran out of turns".

**Why two defences and not one.** ``recursion_limit`` on ``compile()`` bounds the number of
super-steps: it stops a graph that is *looping*. It says nothing about a graph that is
*progressing* - twenty-four legitimate super-steps, each making a 4,000-token call, is well
inside any turn limit and well outside any sane per-request budget. Conversely a tight dollar
ceiling would not stop a cheap infinite loop quickly, because a loop that spends nothing never
trips it. Turn cap plus dollar cap; each catches what the other cannot.

**Money is an integer in 1e-5 USD minor units, never a float.** This is the W6 D4 discipline that
the whole capstone carries, and it is not decoration here. A run that makes forty calls and sums
forty binary fractions accumulates representation error in the number a budget is compared
against; the direction of that error is not controlled, so a ceiling can be crossed without
firing. Integers make the comparison exact. 1e-5 rather than cents because a single Claude call
can legitimately cost less than one cent, and a unit that rounds the common case to zero is a
unit that reports most spend as free.

**This is a fast in-process tally, not the billing source of truth.** The W3 D1 ``llm-proxy``
prices per tenant and is authoritative; this class exists to make an enforcement decision inside
the request, where a round trip to the proxy's ledger would cost more than the call being
guarded. The two are expected to agree closely and are not expected to agree exactly - see
:func:`BudgetGuard.record_call` for the approximation and its provenance.
"""

from __future__ import annotations

from typing import Any, Final

#: Input price in 1e-5 USD per 1,000 tokens for the default model, and output likewise.
#:
#: Illustrative rates, stated as named constants rather than buried in an expression so that
#: repricing is one edit in one place and shows up in a diff as a price change. The authoritative
#: per-tenant rates live in the W3 D1 proxy; these are what the in-process guard estimates with.
PRICE_IN_E5_PER_KTOK: Final[int] = 300
PRICE_OUT_E5_PER_KTOK: Final[int] = 1500


class BudgetExceeded(Exception):
    """Raised when cumulative spend for one request reaches the ceiling.

    Its own type rather than a ``ValueError`` because three layers branch on it specifically: the
    node that lets it propagate untouched, the SSE bridge that emits it as ``3:{"error":
    "budget_exceeded"}``, and the FastAPI handler that maps it to 503. A generic exception would
    make each of those an ``isinstance`` guess.
    """


class BudgetGuard:
    """A per-request cost ceiling, checked before each call and updated after it.

    One instance per request, never shared: the ceiling is *per request*, and a process-wide
    guard would let a busy minute's traffic exhaust one caller's budget with another caller's
    spend. The FastAPI handler constructs one per request and passes it in on the graph's config.

    :ivar _ceiling: The per-request ceiling in 1e-5 USD minor units.
    :ivar _spent: Cumulative spend so far, same units.
    """

    def __init__(self, ceiling_usd_e5: int = 25_000) -> None:
        """Construct a guard with a ceiling in 1e-5 USD minor units.

        :param ceiling_usd_e5: The per-request ceiling. 25000 = $0.25.
        :raises ValueError: if the ceiling is not positive. A zero or negative ceiling would make
            :meth:`check_or_raise` refuse the very first call, which is a misconfiguration worth
            surfacing at construction rather than as a mysterious 503 on every request.
        """
        if ceiling_usd_e5 <= 0:
            raise ValueError(f"ceiling must be positive, got {ceiling_usd_e5}")
        self._ceiling = int(ceiling_usd_e5)
        self._spent = 0

    @property
    def spent_usd_e5(self) -> int:
        """Cumulative spend so far, in 1e-5 USD minor units.

        :returns: The running tally.
        """
        return self._spent

    @property
    def ceiling_usd_e5(self) -> int:
        """The per-request ceiling, in 1e-5 USD minor units.

        :returns: The ceiling this guard was constructed with.
        """
        return self._ceiling

    def check_or_raise(self) -> None:
        """Refuse the next call if the ceiling has already been reached.

        Called *before* a Claude call, not after, and the ordering is the point: checking
        afterwards means the call that breached the budget was already paid for. Checking first
        means the ceiling is a ceiling rather than a report.

        ``>=`` rather than ``>``: reaching the ceiling exactly has consumed the whole budget, and
        a guard that allowed one more call at exactly the limit would let every request exceed
        its ceiling by one call's cost.

        :raises BudgetExceeded: when cumulative spend has reached the ceiling.
        """
        if self._spent >= self._ceiling:
            raise BudgetExceeded(
                f"spent={self._spent} >= ceiling={self._ceiling} (1e-5 USD minor units)"
            )

    def record_call(self, resp: Any) -> None:
        """Add one Anthropic response's cost to the tally.

        The cost is derived from the response's ``usage`` block. A response without one - a
        stubbed client in a test, a future SDK that moves the field - contributes nothing rather
        than raising: a guard that crashed the request it was protecting because it could not
        price a call would be worse than one that under-counts a call it cannot see.

        Integer arithmetic throughout, with the division last. ``(in * 300 + out * 1500) // 1000``
        truncates one sub-minor-unit remainder per call; the alternative, dividing each term
        first, truncates twice per call and drifts further. Truncation rather than rounding is
        deliberate in only this sense: the error is bounded at well under a hundredth of a cent
        per call and the authoritative number is the proxy's, not this one's.

        :param resp: An Anthropic message response, or anything else. Only a ``usage`` attribute
            carrying ``input_tokens`` / ``output_tokens`` is read.
        """
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        self.record_usage(
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
        )

    def record_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Add a token count to the tally directly.

        The counterpart to :meth:`record_call` for a call whose response this process never
        holds. The W7 D3 retrieval pipeline is the case that needs it: it constructs its own
        client, generates, and hands back a result dictionary, so the only thing that crosses
        back into the agent is a pair of numbers. Requiring a response *object* here would have
        left the retrieval agent's second Claude call unbillable and pushed callers into
        fabricating a stub object with a ``usage`` attribute just to satisfy the signature.

        Both methods land on the same arithmetic, so a repricing is still one edit in one place.

        :param input_tokens: Prompt tokens billed.
        :param output_tokens: Completion tokens billed.
        """
        self._spent += (
            input_tokens * PRICE_IN_E5_PER_KTOK + output_tokens * PRICE_OUT_E5_PER_KTOK
        ) // 1000
