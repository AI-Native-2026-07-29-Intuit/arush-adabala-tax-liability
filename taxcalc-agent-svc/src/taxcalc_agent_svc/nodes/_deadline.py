# taxcalc-agent-svc/src/taxcalc_agent_svc/nodes/_deadline.py
"""Per-node deadline: a node that misses its budget degrades instead of hanging.

**The failure this closes.** A node awaiting a cross-encoder forward pass, an MCP tool call or a
generation request has no inherent upper bound on how long it waits. One slow dependency
therefore becomes one request that never completes, which becomes one SSE connection held open
until the client gives up, which - at any real concurrency - becomes a pod holding sockets for
requests nobody is still waiting on. A per-node deadline converts "never returns" into "returns
a known-empty contribution", which the graph can route around.

**Why a sentinel and not an exception.** Raising on timeout would fail the whole run, which is
the wrong answer for a fan-out: a retrieval leg that timed out should leave the api leg's tool
results intact and let synthesis answer from what it has. Returning a *sentinel state slot* -
``{"docs": []}`` for retrieval, ``{"tool_results": {}}`` for the api node - means the timeout is
expressed in the graph's own vocabulary. The reducers then merge it like any other contribution,
and synthesis's refusal path (low confidence, no fabricated citations) is what handles the
genuinely empty case. Graceful degradation, by construction rather than by a try/except at every
call site.

**Why @deadline is applied BEFORE @traceable, and why that ordering is not cosmetic.**
Decorators apply bottom-up, so "applied before" means *closer to the function*, which in source
order means *listed below*::

    @traceable(name="retrieval_agent", project_name=...)   # outer
    @deadline(seconds=3.0, sentinel={"docs": []})          # inner - applied FIRST
    async def retrieval_node(state): ...

That ordering is what makes ``deadline_exceeded=True`` land on the node's own span.
:func:`~langsmith.get_current_run_tree` reads a :class:`~contextvars.ContextVar`, and with
``@traceable`` outermost the node's run tree is set in the very context this wrapper's ``except``
runs in - so the tag lands on the run that actually timed out.

Reversed - ``@deadline`` outermost, wrapping the traced callable - it does not. ``asyncio.wait_for``
schedules its argument as a :class:`~asyncio.Task`, and a Task runs in a *copy* of the context;
``@traceable`` sets the node's run tree inside that copy, the copy dies with the cancelled task,
and the ``except`` here runs in the parent context where the node's run was never visible. The
metadata is then attached to whatever run is current there - the root ``chat_request`` span.

Measured, not reasoned about, because the two orders are indistinguishable from the call site and
both "work" in the sense that the sentinel lands. Driving one slow body through each ordering
under a live run tree::

    deadline outermost -> get_current_run_tree().name == "chat_request"   # the ROOT span
    traceable outermost -> get_current_run_tree().name == "node_B"        # the node's own span

The first is worse than having no tag at all: it marks the whole request as having exceeded a
deadline it never had, so the LangSmith query that is supposed to isolate slow *nodes* returns
the population of slow *requests* instead, and the one node responsible is invisible in both.

Verified rather than assumed: ``tests/test_deadline.py`` fires a synthetic slow body, asserts the
sentinel slot lands within the deadline, and asserts the metadata was recorded against the node's
own run tree rather than against its parent.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from langsmith import get_current_run_tree

#: The metadata key set on the LangSmith run when a node misses its deadline. Queried directly
#: in the LangSmith UI (``metadata.deadline_exceeded = true``) to find the partial-failure
#: population, which is why it is a stable named constant rather than an inline string.
DEADLINE_EXCEEDED_KEY = "deadline_exceeded"

_F = TypeVar("_F", bound=Callable[..., Awaitable[dict[str, Any]]])


def deadline(
    seconds: float, sentinel: dict[str, Any]
) -> Callable[[Callable[..., Awaitable[dict[str, Any]]]], Callable[..., Awaitable[dict[str, Any]]]]:
    """Bound a node body's wall-clock time, landing ``sentinel`` in the state on a miss.

    :param seconds: The wall-clock budget. Taken from
        :class:`~taxcalc_agent_svc.settings.Settings`' per-node fields rather than hard-coded at
        the decoration site, so the three budgets are tunable without a code change.
    :param sentinel: The partial state returned instead of the node's real contribution when the
        budget is missed. Must be a slot the graph declares, or the update is silently dropped -
        which is why each node's sentinel names its own output channel.
    :returns: A decorator that wraps an async node body.

    .. note::
       ``asyncio.wait_for`` cancels the wrapped coroutine on timeout. For the api node that means
       an in-flight MCP tool call is cancelled mid-flight - which is safe precisely because every
       write tool carries a deterministic UUID5 idempotency key, so the retry that follows a
       cancelled refund cannot double-debit. A cancellation-unsafe tool would need its own
       compensation; none of the four here is one.
    """

    def deco(
        fn: Callable[..., Awaitable[dict[str, Any]]],
    ) -> Callable[..., Awaitable[dict[str, Any]]]:
        """Wrap one node body.

        :param fn: The async node body.
        :returns: The deadline-bounded wrapper.
        """

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            """Await ``fn`` under the budget, returning the sentinel on a miss.

            :returns: The node's contribution, or ``sentinel`` if the budget was missed.
            """
            try:
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=seconds)
            except TimeoutError:
                # Live here because @traceable is applied INSIDE this decorator - see the module
                # docstring. `is not None` rather than a bare truthiness check: a RunTree with no
                # children is falsy in some SDK versions, and dropping the tag on exactly the
                # runs that timed out early would be a silent regression.
                run = get_current_run_tree()
                if run is not None:
                    run.add_metadata({DEADLINE_EXCEEDED_KEY: True, "limit_s": seconds})
                # A COPY, never the decorator's own dict. The sentinel is captured once at
                # decoration time and shared by every invocation; returning it directly hands
                # LangGraph's reducers a mutable object that a later merge could write through,
                # making one request's degradation visible in the next one's state.
                return dict(sentinel)

        return wrapper

    return deco
