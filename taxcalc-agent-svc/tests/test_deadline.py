# taxcalc-agent-svc/tests/test_deadline.py
"""The deadline decorator: the sentinel lands, and the tag lands on the right span.

Two separate claims, and the second is the one that is easy to believe without checking. A
timeout that returns the sentinel is visible in any test; a timeout whose
``deadline_exceeded=True`` metadata was attached to the *parent* run is invisible everywhere
except LangSmith, months later, when someone is trying to find out which node is slow and the
query returns whole requests instead.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from langsmith import Client, get_current_run_tree, traceable
from langsmith.run_helpers import tracing_context

from taxcalc_agent_svc.nodes import _deadline as _deadline_mod
from taxcalc_agent_svc.nodes._deadline import DEADLINE_EXCEEDED_KEY, deadline


async def _slow_body(_state: dict[str, Any]) -> dict[str, Any]:
    """A node body that never finishes inside any sane budget.

    :returns: Never reached.
    """
    await asyncio.sleep(5)
    return {"docs": ["real"]}


async def _fast_body(_state: dict[str, Any]) -> dict[str, Any]:
    """A node body that returns immediately.

    :returns: Its real contribution.
    """
    return {"docs": ["real"]}


async def test_the_sentinel_slot_lands_when_the_budget_is_missed() -> None:
    """A slow body contributes the sentinel, not an exception."""
    node = deadline(seconds=0.05, sentinel={"docs": []})(_slow_body)
    assert await node({}) == {"docs": []}


async def test_it_returns_within_the_budget_rather_than_waiting_out_the_body() -> None:
    """The deadline actually bounds wall-clock time.

    Asserted against a generous multiple of the budget rather than the budget itself: the claim
    is "it returns in milliseconds, not in the five seconds the body sleeps", and a tight bound
    here would flake on a loaded CI runner while proving nothing extra.
    """
    node = deadline(seconds=0.05, sentinel={"docs": []})(_slow_body)
    start = time.monotonic()
    await node({})
    assert time.monotonic() - start < 1.0


async def test_a_fast_body_is_untouched() -> None:
    """The decorator is transparent when the budget is met."""
    node = deadline(seconds=5.0, sentinel={"docs": []})(_fast_body)
    assert await node({}) == {"docs": ["real"]}


async def test_the_sentinel_is_copied_not_shared_between_invocations() -> None:
    """Two timeouts return two distinct dicts.

    The sentinel is captured once at decoration time and would otherwise be shared by every
    invocation - so one request's degraded state could be mutated by a reducer and show up in the
    next request's. The two results must be equal and not identical.
    """
    node = deadline(seconds=0.05, sentinel={"docs": []})(_slow_body)
    first = await node({})
    second = await node({})
    assert first == second
    assert first is not second


async def test_the_deadline_tag_lands_on_the_node_span_not_its_parent(
    offline_langsmith_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``deadline_exceeded=True`` is recorded against the node's OWN run, not the root.

    This is the assertion that pins the decorator ORDER, and it is the whole reason this file
    exists. ``@traceable`` must be outermost - i.e. ``@deadline`` applied first - so that the
    node's run tree is the current one when the timeout handler runs. Reversed,
    ``asyncio.wait_for`` runs the traced callable in a copied context that dies with the
    cancelled task, ``get_current_run_tree()`` in the handler falls through to the parent, and
    the tag lands on ``chat_request``. The service still works; the observability quietly lies.

    The run NAME is what is asserted, because that is the only thing that distinguishes the two
    orderings - both produce the sentinel, and both produce a tagged run somewhere.

    ``enabled="local"`` plus the offline client builds real run trees without uploading them, so
    this asserts against the genuine langsmith machinery while making no network call. Plain
    ``enabled=True`` has the background uploader reach for api.smith.langchain.com on every run
    of the suite - measured, and the reason the fixture exists.

    Negative control, run before trusting this: with the decorators reversed the same probe
    reports ``['chat_request']``. The assertion is therefore a gate and not a decoration.
    """
    tagged_on: list[str] = []
    # langsmith's own lookup, imported directly rather than read back off the module under
    # test - the decorator imported the name, it did not define it, so reaching for it there
    # is reaching through a re-export that --strict (rightly) does not consider public.
    real = get_current_run_tree

    def spy() -> Any:
        """Delegate to the real lookup, recording which run it returned.

        Delegating rather than faking is the point: a fake returns whatever this test tells it
        to and would pass under either decorator ordering, which would make the assertion below
        a decoration rather than a gate.

        :returns: Whatever langsmith's own lookup returns.
        """
        run = real()
        tagged_on.append("<none>" if run is None else run.name)
        return run

    # monkeypatch rather than a manual save/restore: it undoes itself even when an assertion
    # raises, and it is the one form mypy accepts for rebinding a name a module imported rather
    # than defined.
    monkeypatch.setattr(_deadline_mod, "get_current_run_tree", spy)

    @traceable(name="retrieval_agent")
    @deadline(seconds=0.05, sentinel={"docs": []})
    async def node(state: dict[str, Any]) -> dict[str, Any]:
        """The node under test, decorated in the production order.

        :returns: The sentinel, via the deadline.
        """
        return await _slow_body(state)

    @traceable(name="chat_request")
    async def root() -> dict[str, Any]:
        """The parent span, so a misattributed tag has somewhere wrong to land.

        :returns: The node's contribution.
        """
        result: dict[str, Any] = await node({})
        return result

    with tracing_context(enabled="local", client=offline_langsmith_client):
        result = await root()

    assert result == {"docs": []}
    # The node's own span, NOT "chat_request" - which is what the reversed order produces.
    assert tagged_on == ["retrieval_agent"]


async def test_the_tag_names_the_limit_that_was_missed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The metadata carries ``limit_s`` alongside the flag.

    Without it, a LangSmith query that finds the timed-out population cannot tell a node that
    missed a 3-second budget from one that missed an 8-second budget - which is the difference
    between "tune the budget" and "the dependency is broken".
    """
    recorded: list[dict[str, Any]] = []

    class FakeRun:
        """A run tree that records what it was tagged with."""

        def add_metadata(self, meta: dict[str, Any]) -> None:
            """Record the metadata.

            :param meta: What the decorator tagged the run with.
            """
            recorded.append(meta)

    monkeypatch.setattr(_deadline_mod, "get_current_run_tree", FakeRun)
    node = deadline(seconds=0.05, sentinel={"docs": []})(_slow_body)
    await node({})

    assert recorded == [{DEADLINE_EXCEEDED_KEY: True, "limit_s": 0.05}]


async def test_no_run_tree_is_not_an_error() -> None:
    """With tracing off there is no run to tag, and the sentinel still lands.

    A decorator that raised ``AttributeError: 'NoneType'`` here would make every local run - and
    every fork PR without a LangSmith secret - fail on the degradation path only.
    """
    with tracing_context(enabled=False):
        node = deadline(seconds=0.05, sentinel={"tool_results": {}})(_slow_body)
        assert await node({}) == {"tool_results": {}}


@pytest.mark.parametrize(
    ("sentinel", "slot"),
    [({"docs": []}, "docs"), ({"tool_results": {}}, "tool_results"), ({"answer": "x"}, "answer")],
)
async def test_each_nodes_sentinel_names_its_own_output_channel(
    sentinel: dict[str, Any], slot: str
) -> None:
    """A sentinel naming a channel the graph does not declare is silently dropped.

    Which is why each node's sentinel names the slot that node writes - a mismatch would make the
    degradation invisible rather than graceful.
    """
    node = deadline(seconds=0.05, sentinel=sentinel)(_slow_body)
    assert slot in await node({})
