# taxcalc-mcp-server/tests/test_tool_descriptions.py
"""The routing-quality gate: tool descriptions are code, and this is their test.

**Why a vague description is worse than a broken tool.** A tool that raises gets an error the
caller can see, report and act on. A tool whose description does not say when to use it simply
never gets called: the model picks something else, answers worse, and nothing anywhere logs a
problem. There is no stack trace for "the model did not consider this tool", which is why the
quality bar has to be enforced mechanically, before the description reaches a client.

**What each rule is actually buying.**

*A length floor* - not because long is good, but because every description that satisfies the
four content rules below is comfortably past it, so a description under the floor is a
description that skipped one of them.

*"Use this"* - a positive trigger. Without it the model must infer applicability from a
restatement of the function name, which is exactly when it guesses.

*"Do NOT"* - a negative boundary, and the one that stops the expensive mistakes. The four tools
here overlap in obvious ways: ``llm.chat`` and ``rag.retrieve_and_generate`` both answer
questions, and ``orders.get_order`` and ``orders.create_refund`` both act on an order. Naming
the neighbour to use instead is what turns an ambiguous choice into a decided one.

*A closing concrete example* - a call and its outcome. Models pattern-match on examples far more
reliably than on prose, and putting it last means it is adjacent to the argument schema the
client reads next.

This file also holds the drift check between the committed ``mcp.json`` and the running server,
because a registration a client trusts and the server does not honour is worse than none at all.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Final

import pytest

from taxcalc_mcp_server import __version__
from taxcalc_mcp_server.app import enforce_strict_tool_schemas, mcp
from taxcalc_mcp_server.tools import _resources, llm, orders, rag  # noqa: F401 - registration

#: Minimum characters. See the module docstring - a floor, not a target.
MIN_DESCRIPTION_CHARS: Final[int] = 200

#: The description must END with a concrete example: the literal "Example:", something after it,
#: and a full stop. Anchored at the end so an example buried mid-paragraph does not satisfy it -
#: the placement is part of the requirement, not an accident of where it was written.
EXAMPLE_PATTERN: Final[re.Pattern[str]] = re.compile(r"Example:\s+\S.*\.\s*$", re.DOTALL)

#: The committed registration the W7 D5 agent reads at startup.
MCP_JSON: Final[Path] = Path(__file__).resolve().parent.parent / "mcp.json"


@pytest.fixture(scope="module", autouse=True)
def _registered() -> None:
    """Harden the schemas once, exactly as the transports do."""
    assert enforce_strict_tool_schemas(mcp) == 4


def _descriptions() -> dict[str, str]:
    """Return each published tool's description.

    :returns: Tool name to description, read from the live registry rather than the source, so
        the gate tests what a client receives.
    """
    return {t.name: t.description or "" for t in asyncio.run(mcp.list_tools())}


@pytest.mark.parametrize("name", sorted(_descriptions()))
def test_description_is_long_enough_to_have_said_something(name: str) -> None:
    """Every description clears the floor."""
    description = _descriptions()[name]
    assert len(description) >= MIN_DESCRIPTION_CHARS, (
        f"{name}: {len(description)} chars, need {MIN_DESCRIPTION_CHARS}"
    )


@pytest.mark.parametrize("name", sorted(_descriptions()))
def test_description_states_when_to_use_the_tool(name: str) -> None:
    """Every description carries a positive trigger."""
    assert "Use this" in _descriptions()[name], f"{name}: no 'Use this ...' trigger"


@pytest.mark.parametrize("name", sorted(_descriptions()))
def test_description_states_when_not_to_use_the_tool(name: str) -> None:
    """Every description carries a negative boundary."""
    assert "Do NOT" in _descriptions()[name], f"{name}: no 'Do NOT ...' boundary"


@pytest.mark.parametrize("name", sorted(_descriptions()))
def test_description_ends_with_a_concrete_example(name: str) -> None:
    """Every description closes with a call and its outcome."""
    description = _descriptions()[name]
    assert EXAMPLE_PATTERN.search(description), f"{name}: no concrete example at the end"


def test_overlapping_tools_name_their_neighbour() -> None:
    """Each tool that has a near-neighbour names it, so the ambiguous choice is decided.

    The four pairs that actually get confused, spelled out rather than assumed: reading an order
    against refunding one, and grounded retrieval against ungrounded chat.
    """
    descriptions = _descriptions()
    assert "orders.create_refund" in descriptions["orders.get_order"]
    assert "orders.get_order" in descriptions["orders.create_refund"]
    assert "rag.retrieve_and_generate" in descriptions["llm.chat"]
    assert "llm.chat" in descriptions["rag.retrieve_and_generate"]


def test_committed_registration_matches_the_running_server() -> None:
    """``mcp.json`` names exactly the tools, resource and version the server publishes.

    The W7 D5 agent reads that file before it connects. A registration that has drifted is a
    contract the client trusts and the server does not honour - a worse failure than having no
    registration at all, because nothing about it looks broken.
    """
    registration = json.loads(MCP_JSON.read_text())
    published = {t.name for t in asyncio.run(mcp.list_tools())}
    resources = {str(r.uri) for r in asyncio.run(mcp.list_resources())}

    assert set(registration["tools"]) == published
    assert set(registration["resources"]) == resources
    assert registration["version"] == __version__
    assert registration["name"] == mcp.name


def test_registration_contains_no_credential() -> None:
    """No token, key or secret is committed in the registration.

    The SSE entry declares the auth *scheme*; it must never carry the credential itself.
    """
    raw = MCP_JSON.read_text()
    # Each needle is ASSEMBLED rather than written out. A scan that spells the literal it looks
    # for becomes a hit for its own search the moment anything greps the tree - which is how the
    # equivalent line in the taxcalc-ai gate first failed the check it describes.
    needles = ("lsv2" + "_pt_", "Bearer " + "ey", "eyJhbGci" + "Oi")
    for needle in needles:
        assert needle not in raw, f"mcp.json contains what looks like a credential: {needle}"
