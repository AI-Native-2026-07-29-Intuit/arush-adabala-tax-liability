# taxcalc-mcp-server/tests/test_resources.py
"""The ``taxcalc://catalogue`` resource: registered, readable, and honest about the corpus.

Two different failures are covered here, and they fail for different reasons.

**Registration and shape.** The W7 D5 agent reads this resource at startup as its fallback path
when ``tools/list`` is unavailable or stale, which means a catalogue that stops being registered
does not break anything until the day the primary path also breaks - a latent failure that shows
up only under the conditions the fallback exists for. Asserting the URI, the MIME type and the
four tool entries keeps that from going unnoticed.

**The corpus size, checked against the seed fixture.** The published size is a literal in
:mod:`taxcalc_mcp_server.tools._resources` rather than a live ``count(*)``, for reasons that
module states. The cost of a literal is that it can drift from the corpus it claims to describe,
so it is compared here against ``taxcalc-ai/tests/fixtures/corpus_seed.jsonl`` - the file the W7
D3 pipeline is actually seeded from. A re-seed that changes the corpus now fails this test
instead of quietly publishing a stale number to every agent that reads the catalogue.

The fixture is reached by relative path because ``taxcalc-ai`` is a *path* dependency of this
project: the sibling checkout is present wherever this suite runs. If it ever is not - a wheel
installed somewhere without the sibling tree - the test skips rather than failing, because its
absence says nothing about whether this server is correct.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any, Final

import pytest

from taxcalc_mcp_server import __version__
from taxcalc_mcp_server.app import mcp
from taxcalc_mcp_server.tools import _resources, llm, orders, rag  # noqa: F401 - registration
from taxcalc_mcp_server.tools._resources import CATALOGUE_URI

#: The W7 D3 seed corpus, relative to this file: ``taxcalc-mcp-server/tests`` -> repo root.
SEED_FIXTURE: Final[Path] = (
    Path(__file__).resolve().parents[2] / "taxcalc-ai" / "tests" / "fixtures" / "corpus_seed.jsonl"
)


def _catalogue() -> dict[str, Any]:
    """Read the catalogue the way a client does, through the resource manager.

    Read through the registry rather than by calling :func:`_resources.catalogue` directly: a
    direct call would pass even if the ``@mcp.resource`` decorator were removed, which is one of
    the two things this module exists to catch.

    :returns: The parsed catalogue payload.
    """
    contents = asyncio.run(mcp.read_resource(CATALOGUE_URI))
    payload = next(iter(contents))
    assert isinstance(payload.content, str)
    parsed: dict[str, Any] = json.loads(payload.content)
    return parsed


def test_the_catalogue_resource_is_registered() -> None:
    """``resources/list`` publishes the catalogue under its committed URI and name."""
    resources = asyncio.run(mcp.list_resources())
    by_uri = {str(r.uri): r for r in resources}
    assert CATALOGUE_URI in by_uri, sorted(by_uri)
    assert by_uri[CATALOGUE_URI].name == "catalogue"
    assert by_uri[CATALOGUE_URI].mimeType == "application/json"


def test_the_catalogue_names_this_server_and_its_version() -> None:
    """The catalogue reports the same version the handshake does, not the SDK's."""
    catalogue = _catalogue()
    assert catalogue["server"] == "taxcalc-mcp-server"
    assert catalogue["version"] == __version__


def test_the_catalogue_lists_every_published_tool() -> None:
    """Every tool in ``tools/list`` has a catalogue entry, and no entry is invented.

    Derived from the live registry on purpose: a catalogue that lists three of four tools sends
    an agent planning against it into a plan that cannot use the fourth.
    """
    published = {t.name for t in asyncio.run(mcp.list_tools())}
    catalogued = {entry["name"] for entry in _catalogue()["tools"]}
    assert catalogued == published


def test_the_write_tool_is_the_only_one_flagged_as_a_write() -> None:
    """``write`` is what the W7 D5 approval gate branches on, so exactly one tool carries it."""
    entries = {entry["name"]: entry for entry in _catalogue()["tools"]}
    assert entries["orders.create_refund"]["write"] is True
    assert [name for name, e in entries.items() if e["write"]] == ["orders.create_refund"]
    assert "idempotency_key" in entries["orders.create_refund"]


def test_the_catalogue_reports_corpus_size_and_tenants() -> None:
    """The corpus block carries both stats the catalogue is required to publish."""
    corpus = _catalogue()["corpus"]
    assert corpus["tenants"] == ["tenant-a", "tenant-b", "tenant-c"]
    size = corpus["size"]
    assert size["chunks"] > 0
    assert size["documents"] > 0
    assert set(size["chunks_per_tenant"]) == set(corpus["tenants"])
    assert sum(size["chunks_per_tenant"].values()) == size["chunks"]


def test_the_published_corpus_size_matches_the_seed_fixture() -> None:
    """The literal size agrees with the corpus the W7 D3 pipeline is actually seeded from.

    This is the assertion that turns a hard-coded number into a checked one. See the module
    docstring for why the number is a literal at all.
    """
    if not SEED_FIXTURE.exists():  # pragma: no cover - only on a checkout without the sibling
        pytest.skip(f"seed corpus not present at {SEED_FIXTURE}")

    rows = [json.loads(line) for line in SEED_FIXTURE.read_text().splitlines() if line.strip()]
    chunks_per_tenant = Counter(row["tenant_id"] for row in rows)
    documents = len({(row["tenant_id"], row["doc_id"]) for row in rows})

    size = _catalogue()["corpus"]["size"]
    assert size["chunks_per_tenant"] == dict(chunks_per_tenant)
    assert size["chunks"] == len(rows)
    assert size["documents"] == documents
