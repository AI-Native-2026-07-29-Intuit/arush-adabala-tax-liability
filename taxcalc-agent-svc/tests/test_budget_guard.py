# taxcalc-agent-svc/tests/test_budget_guard.py
"""BudgetGuard: the ceiling fires at exactly the ceiling, and money stays an integer.

The boundary case is the one worth pinning. ``>=`` rather than ``>`` means reaching the ceiling
exactly has consumed the whole budget; a guard that allowed one more call at exactly the limit
would let every request in the system exceed its ceiling by one call's cost, which at scale is a
systematic overspend that no single request looks responsible for.
"""

from __future__ import annotations

import pytest

from taxcalc_agent_svc.budgets import (
    PRICE_IN_E5_PER_KTOK,
    PRICE_OUT_E5_PER_KTOK,
    BudgetExceeded,
    BudgetGuard,
)


class FakeUsage:
    """An Anthropic-shaped usage block."""

    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        """Construct a usage block.

        :param input_tokens: Prompt tokens.
        :param output_tokens: Generated tokens.
        """
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    """An Anthropic-shaped response carrying a usage block."""

    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        """Construct a response.

        :param input_tokens: Prompt tokens.
        :param output_tokens: Generated tokens.
        """
        self.usage = FakeUsage(input_tokens, output_tokens)


def test_a_fresh_guard_permits_the_first_call() -> None:
    """Nothing spent yet, so nothing is refused."""
    BudgetGuard(25_000).check_or_raise()


def test_it_raises_at_exactly_the_ceiling() -> None:
    """Reaching the ceiling exactly is a breach, not a last free call.

    Spend is driven to precisely 25000 by the pricing arithmetic rather than by poking the
    private tally, so this exercises the same path production does.
    """
    guard = BudgetGuard(25_000)
    # 25000 = (in*300 + out*1500) // 1000 with in=0, out=16667 -> 25000 exactly.
    guard.record_call(FakeResponse(0, 16_667))
    assert guard.spent_usd_e5 == 25_000
    with pytest.raises(BudgetExceeded):
        guard.check_or_raise()


def test_it_permits_one_unit_below_the_ceiling() -> None:
    """The boundary is a boundary in both directions.

    Without this, a guard that raised unconditionally would pass the test above and be
    indistinguishable from a correct one.
    """
    guard = BudgetGuard(25_000)
    guard.record_call(FakeResponse(0, 16_666))
    assert guard.spent_usd_e5 < 25_000
    guard.check_or_raise()


def test_the_tally_is_an_integer_not_a_binary_fraction() -> None:
    """``spent_usd_e5`` is an ``int``.

    This is the W6 D4 money discipline, and it is not decoration: a run that sums forty binary
    fractions accumulates representation error in the very number the ceiling is compared
    against, in a direction nobody controls. A budget that can be crossed without firing is not
    a budget.
    """
    guard = BudgetGuard(25_000)
    guard.record_call(FakeResponse(1_000, 1_000))
    assert isinstance(guard.spent_usd_e5, int)
    assert not isinstance(guard.spent_usd_e5, float)


def test_pricing_matches_the_published_rates() -> None:
    """1,000 in + 1,000 out costs the two rates, summed - with the division taken last."""
    guard = BudgetGuard(25_000)
    guard.record_call(FakeResponse(1_000, 1_000))
    assert guard.spent_usd_e5 == PRICE_IN_E5_PER_KTOK + PRICE_OUT_E5_PER_KTOK


def test_spend_accumulates_across_calls() -> None:
    """Three calls cost three calls. A guard that overwrote would report only the last."""
    guard = BudgetGuard(25_000)
    for _ in range(3):
        guard.record_call(FakeResponse(1_000, 0))
    assert guard.spent_usd_e5 == 3 * PRICE_IN_E5_PER_KTOK


def test_a_response_without_usage_costs_nothing_rather_than_raising() -> None:
    """A stubbed client or a future SDK that moves the field must not crash the request.

    Under-counting a call the guard cannot see is bad; killing the request it was protecting
    because it could not price that call is worse.
    """
    guard = BudgetGuard(25_000)
    guard.record_call(object())
    assert guard.spent_usd_e5 == 0


@pytest.mark.parametrize("bad", [0, -1, -25_000])
def test_a_nonsensical_ceiling_is_rejected_at_construction(bad: int) -> None:
    """A zero or negative ceiling refuses the first call; that is a misconfiguration.

    Surfaced at construction - at process boot - rather than as a mysterious 503 on every
    request, which is the same reasoning the Settings models are built on.
    """
    with pytest.raises(ValueError, match="must be positive"):
        BudgetGuard(bad)


def test_two_guards_do_not_share_a_tally() -> None:
    """The ceiling is PER REQUEST.

    A process-wide guard would let a busy minute's traffic exhaust one caller's budget with
    another caller's spend - a failure that only appears under concurrency and is attributed to
    the wrong request every time.
    """
    a, b = BudgetGuard(25_000), BudgetGuard(25_000)
    a.record_call(FakeResponse(1_000, 1_000))
    assert b.spent_usd_e5 == 0


def test_the_guard_is_readable_for_a_cost_slo(guard: BudgetGuard) -> None:
    """``ceiling_usd_e5`` is exposed so a caller can report headroom, not just breaches."""
    assert guard.ceiling_usd_e5 == 25_000


def test_record_then_check_is_the_order_that_makes_a_ceiling_a_ceiling() -> None:
    """Checking BEFORE the call is what stops the breaching call from being paid for.

    Simulated as a loop: the guard refuses on the iteration after spend reaches the ceiling, so
    the run terminates having spent the ceiling - not the ceiling plus one more call.
    """
    guard = BudgetGuard(1_000)
    calls = 0
    with pytest.raises(BudgetExceeded):
        for _ in range(100):
            guard.check_or_raise()
            guard.record_call(FakeResponse(0, 1_000))  # 1500 e5 per call
            calls += 1
    assert calls == 1
    assert guard.spent_usd_e5 == PRICE_OUT_E5_PER_KTOK
