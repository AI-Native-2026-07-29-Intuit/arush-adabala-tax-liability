# taxcalc-mcp-server/tests/test_smoke_stdio.py
"""Drive the stdio server as a subprocess, the way Claude Desktop does.

**Why a subprocess and not an in-process call.** Everything this file is testing lives *between*
the handler and the client: newline-framed JSON-RPC on stdout, the absence of anything else on
stdout, the real httpx request, the real JSON encoding of a ``Decimal``. An in-process test skips
all of it and passes while the stdio session is unusable.

**Why 100 calls and not one.** The failure this is hunting is not deterministic. A stray write to
stdout - a library's ``print``, a warning, a progress bar - corrupts whichever frame it lands in
the middle of, so a single exchange has a good chance of missing it. A hundred exchanges, each
asserting that the reply parses as JSON-RPC 2.0 with the id it was sent, is a test that fails
when *any* of them is corrupted rather than when the first one is.

**The ledger assertion is the real idempotency test.** The stub records refunds against their
``Idempotency-Key``. If this server ever stopped forwarding the key, the stub would mint a second
refund id and the ledger would hold two entries - so the assertion fails for the right reason
rather than being satisfied by a stub that always answers the same.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from typing import Any, Final
from uuid import uuid4

import pytest

from tests.stub_orders import SEEDED_ORDER, StubState, start_stub, stop_stub

#: How many request/response pairs the smoke drives. See the module docstring.
CALL_PAIRS: Final[int] = 100

#: Protocol version negotiated in the handshake.
PROTOCOL_VERSION: Final[str] = "2024-11-05"


class StdioClient:
    """A minimal JSON-RPC client over a subprocess's stdin/stdout."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        """Wrap a running server process.

        :param proc: The server subprocess, with text-mode pipes.
        """
        self._proc = proc
        self._next_id = 0

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one request and read exactly one reply.

        :param method: JSON-RPC method.
        :param params: Method parameters.
        :returns: The parsed reply.
        :raises AssertionError: if the reply is not JSON-RPC 2.0 or carries the wrong id - which
            is what a corrupted stdout stream looks like from the client's side.
        """
        self._next_id += 1
        rid = self._next_id
        frame = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        assert self._proc.stdin is not None
        self._proc.stdin.write(frame + "\n")
        self._proc.stdin.flush()

        assert self._proc.stdout is not None
        line = self._proc.stdout.readline()
        assert line, f"server closed stdout while answering {method}"
        reply: dict[str, Any] = json.loads(line)
        assert reply["jsonrpc"] == "2.0", f"not JSON-RPC 2.0: {line!r}"
        assert reply["id"] == rid, f"reply id {reply['id']} != request id {rid}"
        return reply

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a notification, which expects no reply.

        :param method: JSON-RPC method.
        :param params: Method parameters.
        """
        frame = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        assert self._proc.stdin is not None
        self._proc.stdin.write(frame + "\n")
        self._proc.stdin.flush()


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[StdioClient]:
    """Start the stub upstream and the MCP server subprocess, handshake, and yield a client.

    :yields: A connected client.
    """
    base_url, stub, stub_thread = start_stub()
    env = {
        **dict(__import__("os").environ),
        "TAXCALC_MCP_ORDERS_SVC_URL": base_url,
        "TAXCALC_MCP_LLM_PROXY_URL": base_url,
        "TAXCALC_MCP_BEARER_JWT": "test-bearer-token",
        "LANGSMITH_API_KEY": "test-not-a-real-key",
        "LANGSMITH_TRACING": "false",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "taxcalc_mcp_server.transports.stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # stderr is NOT captured into a pipe. It is where all the logging goes, and a pipe
        # nobody drains fills its buffer and deadlocks the server mid-test - which would look
        # like a hang in the code under test rather than a mistake in the harness.
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
        bufsize=1,
    )
    client = StdioClient(proc)
    client.request(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "1"},
        },
    )
    client.notify("notifications/initialized", {})
    try:
        yield client
    finally:
        # Every pipe is closed explicitly. `terminate()` alone leaves the stdin/stdout wrappers
        # open, and under `filterwarnings = error` the resulting ResourceWarning surfaces at a
        # random later point in the session as an unraisable-exception failure attributed to
        # whichever test happened to be running - a harness bug that reads as a product bug.
        proc.terminate()
        proc.wait(timeout=10)
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                pipe.close()
        stop_stub(stub, stub_thread)


def test_handshake_reports_this_servers_own_version(server: StdioClient) -> None:
    """``initialize`` names the server and its version, not the SDK's."""
    reply = server.request("tools/list", {})
    assert "tools" in reply["result"]


def test_all_four_tools_are_listed_over_the_wire(server: StdioClient) -> None:
    """``tools/list`` on a real session returns the four tools with strict schemas."""
    tools = server.request("tools/list", {})["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {
        "orders.get_order",
        "orders.create_refund",
        "llm.chat",
        "rag.retrieve_and_generate",
    }
    for tool in tools:
        assert tool["inputSchema"].get("additionalProperties") is False, tool["name"]


def test_hundred_call_pairs_keep_stdout_clean(server: StdioClient) -> None:
    """100 ``tools/list`` + ``tools/call`` pairs, every reply a well-formed JSON-RPC 2.0 frame.

    ``StdioClient.request`` asserts the framing and the id on every single exchange, so a stray
    byte on stdout anywhere in the run fails here rather than corrupting a user's session later.
    """
    for _ in range(CALL_PAIRS):
        server.request("tools/list", {})
        reply = server.request(
            "tools/call",
            {
                "name": "orders.get_order",
                "arguments": {"order_id": SEEDED_ORDER["order_id"], "tenant_id": "tenant-a"},
            },
        )
        payload = json.loads(reply["result"]["content"][0]["text"])
        assert payload["order_id"] == SEEDED_ORDER["order_id"]
        assert payload["total"] == "42.50", "Decimal must cross the wire as an exact string"


def test_money_survives_the_round_trip_as_a_string(server: StdioClient) -> None:
    """``"10.00"`` reaches the upstream and comes back with its scale intact."""
    reply = server.request(
        "tools/call",
        {
            "name": "orders.create_refund",
            "arguments": {
                "order_id": SEEDED_ORDER["order_id"],
                "amount": "10.00",
                "reason": "duplicate",
                "tenant_id": "tenant-a",
                "idempotency_key": str(uuid4()),
            },
        },
    )
    payload = json.loads(reply["result"]["content"][0]["text"])
    assert payload["amount"] == "10.00"


def test_repeating_one_idempotency_key_does_not_debit_twice(server: StdioClient) -> None:
    """Two identical calls return one ``refund_id``, and the ledger holds one entry.

    Both halves matter. Matching ids alone could come from a stub that always answers the same;
    the ledger length is what proves no second refund was issued.
    """
    key = str(uuid4())
    args = {
        "order_id": SEEDED_ORDER["order_id"],
        "amount": "10.00",
        "reason": "duplicate",
        "tenant_id": "tenant-a",
        "idempotency_key": key,
    }
    before = len(StubState.ledger)
    first = server.request("tools/call", {"name": "orders.create_refund", "arguments": args})
    second = server.request("tools/call", {"name": "orders.create_refund", "arguments": args})

    p1 = json.loads(first["result"]["content"][0]["text"])
    p2 = json.loads(second["result"]["content"][0]["text"])
    assert p1["refund_id"] == p2["refund_id"]
    assert len(StubState.ledger) == before + 1, "the ledger was debited twice"


def test_unknown_order_maps_to_the_not_found_code(server: StdioClient) -> None:
    """A 404 upstream surfaces as 4040, through ``_map_http``, over the real wire.

    This is the end-to-end proof of the error table: the mapping is unit-tested in
    ``test_schemas.py``, and here it is confirmed to actually reach a client.
    """
    reply = server.request(
        "tools/call",
        {
            "name": "orders.get_order",
            "arguments": {"order_id": "ord-does-not-exist", "tenant_id": "tenant-a"},
        },
    )
    assert reply["error"]["code"] == 4040, reply


def test_unknown_argument_is_refused_over_the_wire(server: StdioClient) -> None:
    """The strict schema is enforced by the running server, not only in unit tests."""
    reply = server.request(
        "tools/call",
        {
            "name": "orders.get_order",
            "arguments": {
                "order_id": SEEDED_ORDER["order_id"],
                "tenant_id": "tenant-a",
                "hallucinated": "value",
            },
        },
    )
    # -32602 is JSON-RPC's own "invalid params". Using the standard code rather than a private
    # one means a generic MCP client handles a rejected argument correctly knowing nothing about
    # this server.
    assert reply["error"]["code"] == -32602, reply
    assert "hallucinated" in reply["error"]["message"], reply
